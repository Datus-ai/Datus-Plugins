"""Unit tests for datus-aws-common: config, session/AssumeRole, error mapping,
pagination, and the wait_until poller."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from datus_aws_common import (
    ApiError,
    AwsSettings,
    ConfigError,
    PluginError,
    call,
    paginate,
    parse_datetime_arg,
    parse_json_arg,
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
