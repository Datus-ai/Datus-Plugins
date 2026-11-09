"""Behavior contracts for serving-target, context export-dashboard and api call."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
import yaml

from datus_tableau_plugin import cli
from datus_tableau_plugin.cli import main
from datus_tableau_plugin.client import TableauClient
from datus_tableau_plugin.config import Settings
from datus_tableau_plugin.errors import UsageError
from datus_tableau_plugin.output import render
from test_sync import Client

PROFILE = {
    "server_url": "https://tableau.example.test",
    "token_name": "automation",
    "token_secret": "never-print",
    "api_version": "3.21",
}
SERVING_PROFILE = {
    **PROFILE,
    "serving_datasource": "warehouse",
    "serving_database_name": "analytics",
}


# --- serving-target -------------------------------------------------------


def test_serving_target_is_offline_and_reports_configured_datasource(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        cli, "TableauClient", lambda settings: pytest.fail("serving-target connected")
    )
    assert main(["serving-target"], SERVING_PROFILE) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "platform": "tableau",
        "serving_datasource": "warehouse",
        "serving_database_name": "analytics",
    }
    assert captured.err == ""


def test_serving_target_defaults_to_empty_binding(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "TableauClient", lambda settings: pytest.fail("serving-target connected")
    )
    assert main(["serving-target"], PROFILE) == 0
    assert json.loads(capsys.readouterr().out) == {
        "platform": "tableau",
        "serving_datasource": "",
        "serving_database_name": "",
    }


def test_serving_target_supports_yaml_output(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "TableauClient", lambda settings: pytest.fail("serving-target connected")
    )
    assert main(["serving-target", "-o", "yaml"], SERVING_PROFILE) == 0
    assert yaml.safe_load(capsys.readouterr().out) == {
        "platform": "tableau",
        "serving_datasource": "warehouse",
        "serving_database_name": "analytics",
    }


def test_yaml_render_round_trips_tabular_and_scalar_payloads():
    rows = [{"id": "a", "nested": {"x": 1}}, {"id": "b", "nested": None}]
    assert yaml.safe_load(render(rows, "yaml")) == rows
    assert yaml.safe_load(render({"only": "scalar"}, "yaml")) == {"only": "scalar"}


# --- context export-dashboard --------------------------------------------


def test_context_export_dashboard_writes_under_reference_sql_tableau(
    tmp_path, monkeypatch, capsys
):
    class LocalClient(Client):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TableauClient", lambda settings: LocalClient())
    assert (
        main(
            ["context", "export-dashboard", "dashboard:book1:Overview"],
            PROFILE,
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    output_dir = Path(result["output_dir"])
    assert output_dir.parent == tmp_path / "reference_sql" / "tableau"
    assert output_dir.is_dir()
    # The snapshot is independently inspectable offline, like `sync inspect`.
    monkeypatch.setattr(
        cli, "TableauClient", lambda settings: pytest.fail("inspect connected")
    )
    assert main(["sync", "inspect", str(output_dir)], {}) == 0
    assert json.loads(capsys.readouterr().out)["manifest"]["schema_version"] == 1


def test_context_export_dashboard_rejects_unscoped_identifier(
    tmp_path, monkeypatch, capsys
):
    class LocalClient(Client):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_resources(self, *args, **kwargs):
            raise AssertionError("must reject the identifier before discovery")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TableauClient", lambda settings: LocalClient())
    assert main(["context", "export-dashboard", "book1:Overview"], PROFILE) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "scoped dashboard identifiers" in captured.err


# --- api call -------------------------------------------------------------


class ApiClient:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def rest_get(self, path, params=None):
        self.calls.append((path, params))
        return {"path": path, "params": params}


def test_api_call_passes_relative_path_and_parsed_params(monkeypatch, capsys):
    client = ApiClient()
    monkeypatch.setattr(cli, "TableauClient", lambda settings: client)
    assert (
        main(
            ["api", "call", "sites", "--param", "pageSize=1", "--param", "fields=id"],
            PROFILE,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "path": "sites",
        "params": {"pageSize": "1", "fields": "id"},
    }
    assert client.calls == [("sites", {"pageSize": "1", "fields": "id"})]


def test_api_call_rejects_malformed_param_without_connecting(monkeypatch, capsys):
    client = ApiClient()
    monkeypatch.setattr(cli, "TableauClient", lambda settings: client)
    assert main(["api", "call", "sites", "--param", "broken"], PROFILE) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "KEY=VALUE" in captured.err
    assert client.calls == []


_NS = 'xmlns="http://tableau.com/api"'


def _signin_response(url):
    response = requests.Response()
    response.url = url
    response.status_code = 200
    response.headers["Content-Type"] = "application/xml"
    response._content = (
        f'<tsResponse {_NS}><credentials token="session-token">'
        '<site id="site-luid" contentUrl=""/><user id="user-luid"/>'
        "</credentials></tsResponse>"
    ).encode()
    response._content_consumed = True
    return response


def test_rest_get_is_read_only_relative_json_against_the_rest_base(monkeypatch):
    calls = []

    def request(session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/auth/signin"):
            return _signin_response(url)
        response = requests.Response()
        response.url = url
        if url.endswith("/auth/signout"):
            response.status_code = 204
            response._content = b""
        elif urlsplit(url).path.endswith("/api/3.21/sites"):
            response.status_code = 200
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps({"sites": [{"id": "site-1"}]}).encode()
        else:
            raise AssertionError(f"unexpected request {method} {url}")
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    with TableauClient(Settings.from_profile(PROFILE)) as client:
        with pytest.raises(UsageError):
            client.rest_get("https://evil.example.test/api/3.21/sites")
        with pytest.raises(UsageError):
            client.rest_get("../../../etc/passwd")
        data = client.rest_get("sites", {"pageSize": "1"})
    assert data == {"sites": [{"id": "site-1"}]}
    call = next(c for c in calls if c[1].endswith("/api/3.21/sites"))
    assert call[0] == "GET"
    assert (call[2].get("params") or {}).get("pageSize") == "1"
    assert call[2]["headers"]["X-Tableau-Auth"] == "session-token"
    assert {method for method, *_ in calls} <= {"GET", "POST", "DELETE"}
