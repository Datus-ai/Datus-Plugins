from __future__ import annotations

import httpx
import pytest

from datus_grafana_plugin.client import GrafanaClient, safe_path
from datus_grafana_plugin.config import Settings
from datus_grafana_plugin.errors import ApiError, ConfigError, UsageError


def settings(**values):
    return Settings.from_profile({"api_base_url": "https://grafana.test", "auth_mode": "token", "token": "secret", **values})


def test_config_and_path_guard():
    assert settings(api_mode="auto").namespace == "default"
    with pytest.raises(ConfigError):
        Settings.from_profile({"api_base_url": "https://grafana.test", "auth_mode": "basic", "username": "u"})
    for path in ("https://evil/api/health", "/api/../admin", "/public/build.js"):
        with pytest.raises(UsageError):
            safe_path(path)


def test_redirect_is_refused():
    client = GrafanaClient(settings(), transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "https://evil"})))
    with pytest.raises(ApiError, match="refusing redirect"):
        client.request("GET", "/api/health")
    client.close()


@pytest.mark.parametrize("status", [401, 403])
def test_new_dashboard_api_never_falls_back_on_auth_failure(status):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"version": "12.1.0"})
        return httpx.Response(status, json={"message": "denied"})
    client = GrafanaClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError) as error:
        client.dashboard_request("GET", "abc")
    assert error.value.status_code == status
    assert paths == ["/api/health", "/apis/dashboard.grafana.app/v1beta1/namespaces/default/dashboards/abc"]
    client.close()


@pytest.mark.parametrize("status", [404, 405])
def test_new_dashboard_api_falls_back_only_on_capability_failure(status):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"version": "12.1.0"})
        if request.url.path.startswith("/apis/"):
            return httpx.Response(status, json={"message": "not available"})
        return httpx.Response(200, json={"dashboard": {"uid": "abc"}})
    client = GrafanaClient(settings(), transport=httpx.MockTransport(handler))
    assert client.dashboard_request("GET", "abc")["dashboard"]["uid"] == "abc"
    assert paths[-1] == "/api/dashboards/uid/abc"
    client.close()
