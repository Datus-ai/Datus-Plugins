from __future__ import annotations

import httpx
import pytest

from datus_superset_plugin.client import SupersetClient, _safe_relative_path
from datus_superset_plugin.config import Settings
from datus_superset_plugin.errors import ApiError, ConfigError, UsageError


def test_config_auth_modes():
    token = Settings.from_profile({"api_base_url": "https://superset.test/", "auth_mode": "token", "access_token": "x"})
    assert token.base_url == "https://superset.test"
    login = Settings.from_profile({"api_base_url": "https://superset.test", "username": "u", "password": "p"})
    assert login.provider == "db"
    assert not hasattr(login, "serving_datasource")
    assert not hasattr(login, "serving_database_name")
    with pytest.raises(ConfigError):
        Settings.from_profile({"api_base_url": "https://superset.test", "auth_mode": "token"})


@pytest.mark.parametrize("path", ["https://evil.test/api/v1/me/", "/api/v1/../security/login", "/api/v2/me"])
def test_path_guard(path):
    with pytest.raises(UsageError):
        _safe_relative_path(path)


def test_redirect_is_refused_and_token_not_forwarded():
    def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(302, headers={"location": "https://evil.test/steal"})

    settings = Settings.from_profile({"api_base_url": "https://superset.test", "auth_mode": "token", "access_token": "secret"})
    client = SupersetClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError, match="refusing redirect"):
        client.request("GET", "/api/v1/me/")
    client.close()


def test_login_and_csrf_for_write():
    paths = []
    def handler(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path.endswith("/security/login"):
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})
        if request.url.path.endswith("/security/csrf_token/"):
            return httpx.Response(200, json={"result": "csrf"})
        assert request.headers["x-csrftoken"] == "csrf"
        return httpx.Response(200, json={"id": 1})
    settings = Settings.from_profile({"api_base_url": "https://superset.test", "username": "u", "password": "p"})
    client = SupersetClient(settings, transport=httpx.MockTransport(handler))
    assert client.request("POST", "/api/v1/chart/", json_body={"slice_name": "x"}) == {"id": 1}
    assert paths == ["/api/v1/security/login", "/api/v1/security/csrf_token/", "/api/v1/chart/"]
    client.close()
