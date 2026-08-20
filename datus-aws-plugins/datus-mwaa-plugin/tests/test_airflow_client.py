"""MWAA web-session authentication and Airflow REST client tests."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from datus_mwaa_plugin.airflow_client import MwaaAirflowClient


class FakeResponse:
    def __init__(self, status=200, payload=None, text="", content_type=None):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else text
        self.reason = "error" if status >= 400 else "ok"
        self.headers = {
            "content-type": content_type or ("application/json" if payload is not None else "text/plain")
        }

    @property
    def content(self):
        return self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append({"method": method, "path": path, **kwargs})
        return self.routes[(method, path)]


def test_client_logs_in_then_calls_airflow_api(clients):
    clients["mwaa"].set(
        "create_web_login_token",
        {"WebServerHostname": "airflow.example", "WebToken": "short-lived"},
    )
    session = FakeSession({
        ("POST", "/aws_mwaa/login"): FakeResponse(),
        ("GET", "/api/v1/dags"): FakeResponse(payload={"dags": [], "total_entries": 0}),
    })
    client = MwaaAirflowClient(clients["mwaa"], "prod", timeout=12, session=session)
    assert client.request("GET", "/dags", params={"only_active": "true"})["dags"] == []
    assert session.calls[0]["data"] == {"token": "short-lived"}
    assert session.calls[1]["params"] == {"only_active": "true"}
    assert all(call["timeout"] == 12 for call in session.calls)


def test_client_reauthenticates_once_after_401(clients):
    clients["mwaa"].set(
        "create_web_login_token",
        {"WebServerHostname": "airflow.example", "WebToken": "token"},
    )

    class ReauthSession(FakeSession):
        def __init__(self):
            super().__init__({})
            self.api_calls = 0

        def request(self, method, url, **kwargs):
            path = urlsplit(url).path
            self.calls.append({"method": method, "path": path, **kwargs})
            if path == "/aws_mwaa/login":
                return FakeResponse()
            self.api_calls += 1
            return FakeResponse(status=401, payload={"detail": "expired"}) if self.api_calls == 1 else FakeResponse(text="# dag")

    session = ReauthSession()
    client = MwaaAirflowClient(clients["mwaa"], "prod", session=session)
    assert client.request("GET", "/dagSources/token", accept="text/plain") == "# dag"
    assert len([call for call in session.calls if call["path"] == "/aws_mwaa/login"]) == 2
