from __future__ import annotations

import json
from pathlib import Path

import pytest

from datus_grafana_plugin.errors import UsageError
from datus_grafana_plugin.exporter import classify_query, export_dashboard


class Client:
    def dashboard_request(self, method, uid):
        return {
            "dashboard": {
                "uid": uid, "title": "Service Signals", "password": "bad",
                "templating": {"list": [{"name": "cluster", "current": {"value": "prod"}}]},
                "panels": [{
                    "id": 1, "title": "All languages", "datasource": {"uid": "prom", "type": "prometheus"},
                    "targets": [
                        {"refId": "A", "rawSql": "select * from t where ts > $__timeFrom() and cluster = '$cluster'"},
                        {"refId": "B", "expr": "rate(http_requests_total[$__rate_interval])"},
                        {"refId": "C", "expr": "{app=\"api\"}", "datasource": {"uid": "loki", "type": "loki"}},
                        {"refId": "D", "query": "from(bucket: \"b\")", "queryType": "flux"},
                        {"refId": "E", "type": "math", "expression": "$A / $B", "datasource": "__expr__"},
                        {"refId": "F", "model": {"opaque": True}},
                    ],
                }],
            },
            "meta": {"folderUid": "f"},
        }

    def request(self, method, path, **kwargs):
        uid = path.rsplit("/", 1)[-1]
        types = {"prom": "prometheus", "loki": "loki"}
        return {"uid": uid, "type": types.get(uid, "unknown"), "secureJsonData": {"token": "bad"}}


def test_classification_handles_string_expression_datasource():
    assert classify_query({"datasource": "__expr__", "type": "math", "expression": "$A"}, None)[0] == "grafana-expression"


def test_all_targets_export_with_macros_manifest_and_redaction(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = export_dashboard(Client(), "dash", instance_url="https://grafana.test")
    directory = Path(result["output_dir"])
    assert result["total"] == 6
    manifest = json.loads((directory / "manifest.json").read_text())
    assert {q["language"] for q in manifest["queries"]} == {"sql", "promql", "logql", "flux", "grafana-expression", "unknown"}
    sql = next(directory.glob("*.sql")).read_text()
    assert "$__timeFrom()" in sql and "$cluster" in sql
    assert "bad" not in (directory / "_source/dashboard.json").read_text()
    assert len([p for p in directory.iterdir() if p.is_file() and p.name != "manifest.json"]) == 6
    with pytest.raises(UsageError):
        export_dashboard(Client(), "dash", instance_url="https://grafana.test")


def test_output_root_must_be_in_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageError):
        export_dashboard(Client(), "dash", output_root="../escape", instance_url="https://grafana.test")
