from __future__ import annotations

import json
from pathlib import Path

import pytest

from datus_superset_plugin.errors import UsageError
from datus_superset_plugin.exporter import _variables, export_dashboard


class Client:
    def request(self, method, path, **kwargs):
        if path == "/api/v1/dashboard/7":
            return {"result": {"id": 7, "dashboard_title": "Revenue Overview", "password": "bad"}}
        if path.endswith("/charts"):
            return {"result": [{"id": 11, "slice_name": "Revenue"}, {"id": 12, "slice_name": "Broken"}]}
        if path == "/api/v1/chart/11":
            return {"result": {"id": 11, "slice_name": "Revenue", "query_context": json.dumps({"queries": [{}]}), "password": "bad", "template": "$region ${tenant:sqlstring}"}}
        if path == "/api/v1/chart/12":
            return {"result": {"id": 12, "slice_name": "Broken"}}
        if path == "/api/v1/chart/data":
            return {"result": [{"query": "SELECT * FROM sales WHERE region = '{{ filter_values(\"region\") }}'"}]}
        if path.endswith("/data/"):
            return {}
        raise AssertionError(path)


def test_export_is_atomic_redacted_and_manifested(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = export_dashboard(Client(), "7", instance_url="https://superset.test")
    directory = Path(result["output_dir"])
    assert result == {"output_dir": str(directory), "total": 2, "succeeded": 1, "failed": 1}
    sql = next(directory.glob("*.sql")).read_text()
    assert "-- Dashboard=Revenue Overview;" in sql
    assert "filter_values" in sql
    manifest = json.loads((directory / "manifest.json").read_text())
    assert [q["status"] for q in manifest["queries"]] == ["ok", "failed"]
    assert "bad" not in (directory / "_source/dashboard.json").read_text()
    with pytest.raises(UsageError):
        export_dashboard(Client(), "7", instance_url="https://superset.test")


def test_variables_are_names_not_regex_pairs():
    assert _variables({"x": "$region ${tenant:sqlstring}"}) == ["region", "tenant"]


def test_output_cannot_escape_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageError):
        export_dashboard(Client(), "7", output_root="../escape", instance_url="https://superset.test")
