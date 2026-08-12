from __future__ import annotations

import argparse

from datus_grafana_plugin.cli import _panel_command, build_parser
from datus_grafana_plugin.config import Settings


class Client:
    def __init__(self):
        self.saved = None

    def dashboard_request(self, method, uid, **kwargs):
        if method == "GET":
            return {"dashboard": {"uid": uid, "title": "D", "panels": [{"id": 2, "title": "P", "targets": []}]}, "meta": {}}
        self.saved = kwargs["json_body"]
        return {"status": "success"}

    def request(self, method, path, **kwargs):
        self.saved = kwargs["json_body"]
        return {"results": {}}


def settings():
    return Settings.from_profile({"api_base_url": "https://grafana.test", "token": "x"})


def test_parser_has_dashboard_panel_and_context_commands():
    parser = build_parser()
    assert parser.parse_args(["dashboards", "create", "--json", "{}"]).__dict__["dashboard_action"] == "create"
    assert parser.parse_args(["queries", "run-panel", "d", "2"]).func.__name__ == "_run_panel_query"
    assert parser.parse_args(["context", "export-dashboard", "d"]).func.__name__ == "_export_context"


def test_panel_create_assigns_next_id_and_saves():
    client = Client()
    ns = argparse.Namespace(subcommand="create", dashboard_uid="d", json_body='{"title":"new"}', json_file=None)
    result = _panel_command(client, settings(), ns)
    assert result["panel"]["id"] == 3
    assert client.saved["dashboard"]["panels"][-1]["title"] == "new"
