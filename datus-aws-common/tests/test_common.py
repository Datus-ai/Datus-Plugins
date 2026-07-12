"""Unit tests for datus-aws-common: config, session/AssumeRole, error mapping,
pagination, and the wait_until poller."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from datus_aws_common import (
    AWS_KEYS,
    ApiError,
    AwsSettings,
    ConfigError,
    PluginError,
    aws_config_schema,
    call,
    paginate,
    parse_datetime_arg,
    parse_json_arg,
    validate_aws_profile,
    validate_keys,
    wait_until,
)
from datus_aws_common import session as session_mod


# ------------------------------------------------------------------ config


def test_aws_settings_parses_credentials_and_numbers():
    s = AwsSettings.from_profile(
        {
            "region": "us-west-2",
            "profile": "prod",
            "role_arn": "arn:aws:iam::1:role/r",
            "timeout": "45",
            "max_attempts": "5",
            "": "ignored-empty-key-value",
        }
    )
    assert s.region == "us-west-2"
    assert s.profile == "prod"
    assert s.role_arn == "arn:aws:iam::1:role/r"
    assert s.timeout == 45.0
    assert s.max_attempts == 5


def test_aws_settings_bad_number_is_config_error():
    with pytest.raises(ConfigError):
        AwsSettings.from_profile({"timeout": "soon"})


def test_validate_keys_allows_aws_framework_and_plugin_keys():
    validate_keys(
        {"region": "us-east-1", "name": "p", "default": True, "catalog_id": "1"},
        {"catalog_id"},
        "plugins.glue.<profile>",
    )


def test_validate_keys_rejects_unknown():
    with pytest.raises(ConfigError) as exc:
        validate_keys({"regionn": "us-east-1"}, set(), "plugins.glue.<profile>")
    assert "regionn" in str(exc.value)


def test_aws_config_schema_covers_shared_keys_and_flags_secrets():
    schema = aws_config_schema()
    names = [f["name"] for f in schema]
    assert set(names) == set(AWS_KEYS)  # exactly the shared credential/session keys
    assert names[0] == "region"  # shared keys lead the form
    for field in schema:
        assert field.get("name") and field.get("description")
    secret = {f["name"] for f in schema if f.get("secret")}
    assert secret == {"secret_access_key", "session_token"}
    defaults = {f["name"]: f.get("default") for f in schema}
    assert defaults["timeout"] is not None and defaults["max_attempts"] is not None


def test_aws_config_schema_appends_extras_and_returns_copies():
    schema = aws_config_schema(extra_fields=[{"name": "bucket", "description": "b"}])
    assert [f["name"] for f in schema][-1] == "bucket"  # extras appended after shared keys
    # mutating the result must not corrupt the shared template
    schema[0]["name"] = "MUTATED"
    assert aws_config_schema()[0]["name"] == "region"


def test_validate_aws_profile_treats_placeholders_as_opaque():
    assert validate_aws_profile(
        {"secret_access_key": "${S}", "endpoint_url": "${E}", "timeout": "${T}"}
    ) == []


def test_validate_aws_profile_reports_problems():
    assert validate_aws_profile({"nope": 1}) == ["unknown key(s): nope"]
    assert validate_aws_profile({"endpoint_url": "ftp://x"}) == [
        "endpoint_url must start with http:// or https://"
    ]
    assert validate_aws_profile({"timeout": "soon"})  # non-numeric caught


def test_validate_aws_profile_honours_extra_and_required_keys():
    # a plugin's own key is accepted once declared as extra
    assert validate_aws_profile({"catalog_id": "1"}, extra_keys=("catalog_id",)) == []
    # required keys are enforced (but a placeholder counts as present)
    assert validate_aws_profile({}, required=("aws_account_id",)) == ["aws_account_id is required"]
    assert validate_aws_profile(
        {"aws_account_id": "${ACCT}"}, extra_keys=("aws_account_id",), required=("aws_account_id",)
    ) == []


# --------------------------------------------------------------- session


class _FakeSts:
    last_assume = None

    def assume_role(self, **kwargs):
        _FakeSts.last_assume = kwargs
        return {
            "Credentials": {
                "AccessKeyId": "TMP_AK",
                "SecretAccessKey": "TMP_SK",
                "SessionToken": "TMP_TOKEN",
            }
        }


class _FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.region_name = kwargs.get("region_name")
        self.built_clients = []

    def client(self, service, **kwargs):
        self.built_clients.append((service, kwargs))
        if service == "sts":
            return _FakeSts()
        return ("client", service, kwargs)


class _FakeSessionFactory:
    def __init__(self):
        self.calls = []

    def Session(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeSession(**kwargs)


class _FakeBoto3:
    def __init__(self):
        self.session = _FakeSessionFactory()


@pytest.fixture
def fake_boto3(monkeypatch):
    fake = _FakeBoto3()
    monkeypatch.setattr(session_mod, "_import_boto3", lambda: fake)
    return fake


def test_build_session_assume_role_uses_temp_credentials(fake_boto3):
    s = AwsSettings.from_profile(
        {
            "region": "eu-west-1",
            "access_key_id": "AK",
            "secret_access_key": "SK",
            "role_arn": "arn:aws:iam::123:role/datus",
            "external_id": "team-a",
        }
    )
    session_mod.build_session(s)

    # first session bootstrapped from explicit keys + region
    first = fake_boto3.session.calls[0]
    assert first["aws_access_key_id"] == "AK"
    assert first["region_name"] == "eu-west-1"
    # assume_role was called with the role + external id
    assert _FakeSts.last_assume["RoleArn"] == "arn:aws:iam::123:role/datus"
    assert _FakeSts.last_assume["ExternalId"] == "team-a"
    # second session uses the returned temp credentials
    second = fake_boto3.session.calls[1]
    assert second["aws_access_key_id"] == "TMP_AK"
    assert second["aws_session_token"] == "TMP_TOKEN"


def test_build_client_applies_endpoint_and_config(fake_boto3):
    s = AwsSettings.from_profile({"region": "us-east-1", "endpoint_url": "http://minio:9000"})
    session = _FakeSession(region_name="us-east-1")
    session_mod.build_client(s, "s3", session=session)
    service, kwargs = session.built_clients[0]
    assert service == "s3"
    assert kwargs["endpoint_url"] == "http://minio:9000"
    assert kwargs["config"] is not None


# ----------------------------------------------------------- error mapping


def test_call_maps_client_error_to_api_error():
    err = ClientError(
        {
            "Error": {"Code": "AccessDeniedException", "Message": "not allowed"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "GetTable",
    )

    def boom(**kwargs):
        raise err

    with pytest.raises(ApiError) as exc:
        call(boom, Name="t")
    assert exc.value.status_code == 403
    assert "AccessDeniedException: not allowed" in str(exc.value)


def test_call_passes_through_success():
    assert call(lambda **kw: {"ok": kw}, a=1) == {"ok": {"a": 1}}


# ------------------------------------------------------------- pagination


class _FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.pages)


class _FakePageableClient:
    def __init__(self, pages):
        self._paginator = _FakePaginator(pages)

    def can_paginate(self, op):
        return True

    def get_paginator(self, op):
        return self._paginator


class _FakeSingleClient:
    def can_paginate(self, op):
        return False

    def list_things(self, **kwargs):
        return {"Items": [1, 2, 3]}


def test_paginate_collects_all_pages():
    client = _FakePageableClient([{"Items": [1, 2]}, {"Items": [3]}])
    assert paginate(client, "list_things", "Items") == [1, 2, 3]


def test_paginate_honours_limit():
    client = _FakePageableClient([{"Items": [1, 2]}, {"Items": [3, 4]}])
    assert paginate(client, "list_things", "Items", limit=3) == [1, 2, 3]


def test_paginate_non_pageable_single_call():
    assert paginate(_FakeSingleClient(), "list_things", "Items") == [1, 2, 3]


# --------------------------------------------------------------- wait_until


def test_wait_until_reaches_terminal_and_reports_changes():
    states = iter(["PENDING", "RUNNING", "RUNNING", "SUCCESS"])
    seen = []
    final = wait_until(
        lambda: next(states),
        lambda s: s in ("SUCCESS", "FAILED"),
        timeout=100,
        interval=0,
        on_change=seen.append,
        sleep=lambda s: None,
    )
    assert final == "SUCCESS"
    assert seen == ["PENDING", "RUNNING", "SUCCESS"]  # duplicates collapsed


def test_wait_until_times_out():
    with pytest.raises(PluginError):
        wait_until(
            lambda: "RUNNING",
            lambda s: False,
            timeout=0,
            interval=0,
            sleep=lambda s: None,
        )


# ------------------------------------------------------------- cli helpers


def test_parse_json_arg_rejects_bad_json():
    from datus_aws_common import UsageError

    with pytest.raises(UsageError):
        parse_json_arg("{not json", "--conf")


def test_parse_datetime_arg_accepts_iso_and_z():
    dt = parse_datetime_arg("2026-07-01T00:00:00Z", "--start")
    assert dt.tzinfo is not None and dt.year == 2026
