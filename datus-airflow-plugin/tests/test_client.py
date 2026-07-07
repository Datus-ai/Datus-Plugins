"""AirflowClient: auth flows, pagination, error extraction, URL handling."""

from __future__ import annotations

import base64
import json
import time

import pytest

from datus_airflow_plugin.client import AirflowClient
from datus_airflow_plugin.config import Settings
from datus_airflow_plugin.errors import ApiError, ConfigError

from conftest import BASE_URL, FakeResponse, paged


def make_settings(tmp_path, **extra) -> Settings:
    profile = {"api_base_url": BASE_URL, "cache_dir": str(tmp_path / "cache"), **extra}
    return Settings.from_profile(profile)


def make_jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{payload}.sig"


def test_static_token_is_sent_as_bearer(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="tok123"), session=fake_session)
    fake_session.add("GET", "/api/v2/version", FakeResponse(json_data={"version": "3.1.6"}))
    assert client.request("GET", "/version")["version"] == "3.1.6"
    assert fake_session.calls[0]["headers"]["Authorization"] == "Bearer tok123"


def test_username_password_login_then_request(fake_session, tmp_path):
    settings = make_settings(tmp_path, username="admin", password="pw")
    client = AirflowClient(settings, session=fake_session)
    token = make_jwt(time.time() + 3600)
    fake_session.add("POST", "/auth/token", FakeResponse(json_data={"access_token": token}))
    fake_session.add("GET", "/api/v2/version", FakeResponse(json_data={"version": "3.1.6"}))

    client.request("GET", "/version")
    login_calls = fake_session.calls_to("POST", "/auth/token")
    assert login_calls[0]["json"] == {"username": "admin", "password": "pw"}
    assert fake_session.calls_to("GET", "/api/v2/version")[0]["headers"]["Authorization"] == f"Bearer {token}"

    # second request reuses the in-memory token: still exactly one login
    client.request("GET", "/version")
    assert len(fake_session.calls_to("POST", "/auth/token")) == 1


def test_token_cached_on_disk_and_reused(fake_session, tmp_path):
    settings = make_settings(tmp_path, username="admin", password="pw")
    token = make_jwt(time.time() + 3600)
    fake_session.add("POST", "/auth/token", FakeResponse(json_data={"access_token": token}))
    fake_session.add("GET", "/api/v2/version", FakeResponse(json_data={"version": "3.1.6"}))

    AirflowClient(settings, session=fake_session).request("GET", "/version")
    # a fresh client (new process simulation) loads the cached token, no login
    AirflowClient(settings, session=fake_session).request("GET", "/version")
    assert len(fake_session.calls_to("POST", "/auth/token")) == 1


def test_expired_cached_token_triggers_relogin(fake_session, tmp_path):
    settings = make_settings(tmp_path, username="admin", password="pw")
    expired = make_jwt(time.time() - 10)
    fresh = make_jwt(time.time() + 3600)
    fake_session.add(
        "POST",
        "/auth/token",
        [FakeResponse(json_data={"access_token": expired}), FakeResponse(json_data={"access_token": fresh})],
    )
    fake_session.add("GET", "/api/v2/version", FakeResponse(json_data={"version": "3.1.6"}))

    AirflowClient(settings, session=fake_session).request("GET", "/version")
    AirflowClient(settings, session=fake_session).request("GET", "/version")
    assert len(fake_session.calls_to("POST", "/auth/token")) == 2


def test_401_relogins_once_and_retries(fake_session, tmp_path):
    settings = make_settings(tmp_path, username="admin", password="pw")
    opaque = "opaque-token"  # no exp claim -> only the server can invalidate it
    fake_session.add("POST", "/auth/token", FakeResponse(json_data={"access_token": opaque}))
    fake_session.add(
        "GET",
        "/api/v2/dags",
        [
            FakeResponse(401, json_data={"detail": "expired"}),
            FakeResponse(json_data=paged("dags", [])),
        ],
    )
    client = AirflowClient(settings, session=fake_session)
    assert client.request("GET", "/dags") == paged("dags", [])
    assert len(fake_session.calls_to("GET", "/api/v2/dags")) == 2


def test_401_with_static_token_raises(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="bad"), session=fake_session)
    fake_session.add("GET", "/api/v2/dags", FakeResponse(401, json_data={"detail": "nope"}))
    with pytest.raises(ApiError) as exc:
        client.request("GET", "/dags")
    assert exc.value.status_code == 401
    assert "rejected" in str(exc.value)


def test_no_credentials_401_surfaces_as_api_error(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path), session=fake_session)
    fake_session.add("GET", "/api/v2/dags", FakeResponse(401, json_data={"detail": "auth required"}))
    with pytest.raises(ApiError):
        client.request("GET", "/dags")


def test_validation_error_detail_is_flattened(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="t"), session=fake_session)
    fake_session.add(
        "POST",
        "/api/v2/pools",
        FakeResponse(
            422,
            json_data={"detail": [{"loc": ["body", "slots"], "msg": "field required", "type": "missing"}]},
        ),
    )
    with pytest.raises(ApiError) as exc:
        client.request("POST", "/pools", json_body={})
    assert "body.slots: field required" in str(exc.value)


def test_base_url_normalization():
    s = Settings.from_profile({"api_base_url": "http://host:8080/api/v2/"})
    assert s.base_url == "http://host:8080"
    with pytest.raises(ConfigError):
        Settings.from_profile({"api_base_url": "host:8080"})


def test_paginate_walks_all_pages(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="t"), session=fake_session)

    def pages(call):
        offset = call["params"]["offset"]
        items = [{"key": f"v{i}"} for i in range(offset, min(offset + 50, 120))]
        return FakeResponse(json_data={"variables": items, "total_entries": 120})

    fake_session.add("GET", "/api/v2/variables", pages)
    rows = client.paginate("/variables", "variables")
    assert len(rows) == 120
    assert rows[0]["key"] == "v0" and rows[-1]["key"] == "v119"
    assert [c["params"]["offset"] for c in fake_session.calls] == [0, 50, 100]


def test_paginate_respects_limit(fake_session, tmp_path):
    client = AirflowClient(make_settings(tmp_path, token="t"), session=fake_session)

    def pages(call):
        offset, limit = call["params"]["offset"], call["params"]["limit"]
        items = [{"key": f"v{i}"} for i in range(offset, offset + limit)]
        return FakeResponse(json_data={"variables": items, "total_entries": 1000})

    fake_session.add("GET", "/api/v2/variables", pages)
    rows = client.paginate("/variables", "variables", limit=70)
    assert len(rows) == 70
    assert [c["params"]["limit"] for c in fake_session.calls] == [50, 20]


def test_verify_ssl_passthrough(fake_session, tmp_path):
    settings = make_settings(tmp_path, token="t", verify_ssl="/etc/ca.pem")
    client = AirflowClient(settings, session=fake_session)
    fake_session.add("GET", "/api/v2/version", FakeResponse(json_data={}))
    client.request("GET", "/version")
    assert fake_session.calls[-1]["verify"] == "/etc/ca.pem"
