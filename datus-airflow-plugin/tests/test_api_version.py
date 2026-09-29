"""api_version resolution (URL suffix / explicit / server probe) and the
Airflow-3-only command guard."""

from __future__ import annotations

import pytest

from conftest import BASE_URL, FakeResponse, paged
from datus_airflow_plugin.cli import Context, main
from datus_airflow_plugin.client import AirflowClient
from datus_airflow_plugin.config import Settings
from datus_airflow_plugin.errors import ApiError, ConfigError, UsageError


def make_settings(tmp_path, **extra) -> Settings:
    return Settings.from_profile(
        {"name": "dev", "api_base_url": BASE_URL, "cache_dir": str(tmp_path / "cache"), **extra}
    )


# ------------------------------------------------------------ config parsing


@pytest.mark.parametrize(
    "url, version, expected",
    [
        (BASE_URL, None, "auto"),
        (BASE_URL, "auto", "auto"),
        (BASE_URL + "/api/v1", None, "v1"),
        (BASE_URL + "/api/v2/", "auto", "v2"),
        (BASE_URL, "v1", "v1"),
        (BASE_URL, 2, "v2"),
        (BASE_URL + "/api/v2", "v1", "v1"),  # explicit wins over the suffix
    ],
)
def test_api_version_resolution_without_network(url, version, expected):
    settings = Settings.from_profile({"api_base_url": url, "api_version": version})
    assert settings.api_version == expected


# -------------------------------------------------------------------- probe


def test_auto_probes_v2_once_and_unauthenticated(fake_session, tmp_path):
    settings = make_settings(tmp_path, token="tok", timeout=7, verify_ssl="/etc/ca.pem")
    client = AirflowClient(settings, session=fake_session)
    assert not fake_session.calls  # construction never probes
    fake_session.add("GET", "/api/v2/version", FakeResponse(json_data={"version": "3.1.6"}))
    fake_session.add("GET", "/api/v2/dags", FakeResponse(json_data=paged("dags", [])))

    client.request("GET", "/dags")
    client.request("GET", "/dags")

    probe = fake_session.calls[0]
    assert probe["path"] == "/api/v2/version"
    assert "Authorization" not in probe["headers"]
    assert probe["timeout"] == 7 and probe["verify"] == "/etc/ca.pem"
    assert len(fake_session.calls_to("GET", "/api/v2/version")) == 1
    assert settings.api_version == "v2"


def test_auto_detects_airflow_2_and_uses_basic_auth(fake_session, tmp_path):
    """The reported bug: a 2.x server has no /api/v2 and no /auth/token."""
    settings = make_settings(tmp_path, username="admin", password="pw")
    client = AirflowClient(settings, session=fake_session)
    fake_session.add("GET", "/api/v1/version", FakeResponse(json_data={"version": "2.10.4"}))
    fake_session.add("GET", "/api/v1/dags", FakeResponse(json_data=paged("dags", [])))

    client.request("GET", "/dags")

    assert [c["path"] for c in fake_session.calls] == [
        "/api/v2/version",
        "/api/v1/version",
        "/api/v1/dags",
    ]
    assert fake_session.calls_to("GET", "/api/v1/dags")[0]["auth"] == ("admin", "pw")
    assert not fake_session.calls_to("POST", "/auth/token")
    assert client.is_v1 and settings.api_version == "v1"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_protected_version_endpoint_still_counts(fake_session, tmp_path, status):
    client = AirflowClient(make_settings(tmp_path, token="t"), session=fake_session)
    fake_session.add("GET", "/api/v2/version", FakeResponse(status, json_data={"detail": "no"}))
    assert client.api_version == "v2"


def test_html_catch_all_is_not_mistaken_for_v2(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="t"), session=fake_session)
    fake_session.add("GET", "/api/v2/version", FakeResponse(200, text="<html></html>", content_type="text/html"))
    fake_session.add("GET", "/api/v1/version", FakeResponse(json_data={"version": "2.10.4"}))
    assert client.api_version == "v1"


def test_undetectable_server_names_both_probes(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="t"), session=fake_session)
    with pytest.raises(ConfigError) as exc:
        client.request("GET", "/dags")
    message = str(exc.value)
    assert f"{BASE_URL}/api/v2/version" in message
    assert f"{BASE_URL}/api/v1/version" in message
    assert "api_version" in message


def test_explicit_version_never_probes(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="t", api_version="v1"), session=fake_session)
    fake_session.add("GET", "/api/v1/dags", FakeResponse(json_data=paged("dags", [])))
    client.request("GET", "/dags")
    assert [c["path"] for c in fake_session.calls] == ["/api/v1/dags"]


def test_login_404_hints_at_airflow_2(fake_session, tmp_path):
    settings = make_settings(tmp_path, api_version="v2", username="admin", password="pw")
    client = AirflowClient(settings, session=fake_session)
    with pytest.raises(ApiError) as exc:
        client.request("GET", "/dags")
    message = str(exc.value)
    assert "HTTP 404" in message
    assert "Airflow 2.x" in message and "api_version: v1" in message


# -------------------------------------------------------------------- guard


@pytest.mark.parametrize(
    "argv",
    [["assets", "list"], ["backfill", "list", "--dag-id", "etl"], ["jobs", "check"]],
)
def test_airflow3_groups_fail_fast_on_v1(argv, capsys):
    # explicit v1 never probes, so no network is involved
    rc = main(argv, {"name": "legacy", "api_base_url": BASE_URL, "api_version": "v1"})
    assert rc == 2
    err = capsys.readouterr().err
    assert f"`{argv[0]}` requires Airflow 3 (REST API v2); profile legacy targets v1" in err


def test_guard_probes_under_auto_before_any_command_request(fake_session, tmp_path):
    ctx = Context(make_settings(tmp_path, token="t"))
    ctx._client = AirflowClient(ctx.settings, session=fake_session)
    fake_session.add("GET", "/api/v1/version", FakeResponse(json_data={"version": "2.10.4"}))

    with pytest.raises(UsageError) as exc:
        ctx.check_group_supported("assets")
    assert "profile dev targets v1" in str(exc.value)
    assert [c["path"] for c in fake_session.calls] == ["/api/v2/version", "/api/v1/version"]


def test_guard_lets_other_groups_and_v2_through(fake_session, tmp_path):
    ctx = Context(make_settings(tmp_path, token="t", api_version="v1"))
    ctx._client = AirflowClient(ctx.settings, session=fake_session)
    ctx.check_group_supported("dags")

    ctx = Context(make_settings(tmp_path, token="t", api_version="v2"))
    ctx._client = AirflowClient(ctx.settings, session=fake_session)
    for group in ("assets", "backfill", "jobs"):
        ctx.check_group_supported(group)
    assert not fake_session.calls
