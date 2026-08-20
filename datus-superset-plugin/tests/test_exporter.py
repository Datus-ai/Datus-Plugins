from __future__ import annotations

import json
from pathlib import Path

import pytest

from datus_superset_plugin.errors import PluginError, UsageError
from datus_superset_plugin.exporter import (
    _synthesized_context,
    _variables,
    discover_dashboard_candidates,
    export_dashboard,
)


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


def test_export_filters_chart_ids_and_emits_generic_handoff_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = export_dashboard(
        Client(),
        "7",
        chart_ids=["11"],
        instance_url="https://superset.test",
        profile_name="prod",
    )

    assert result["total"] == 1
    manifest = json.loads((Path(result["output_dir"]) / "manifest.json").read_text())
    assert manifest["contract"] == "dashboard-sql-export/v1"
    assert manifest["plugin"] == "superset"
    assert manifest["profile"] == "prod"
    assert "serving_datasource" not in manifest
    assert "serving_database_name" not in manifest
    assert manifest["dashboard"]["name"] == "Revenue Overview"
    assert manifest["selection"] == {"mode": "selective", "chart_ids": ["11"]}
    query = manifest["queries"][0]
    assert query["id"] == "chart-11-query-1"
    assert query["candidate_id"] == "chart-11"
    assert query["sql_file"] == query["file"]
    assert query["checksum"] == f"sha256:{query['sha256']}"
    assert query["status"] == "ok"
    assert not (Path(result["output_dir"]) / "_source/chart-12.json").exists()


def test_export_rejects_unknown_selected_chart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PluginError, match="no dashboard query could be exported"):
        export_dashboard(Client(), "7", chart_ids=["999"], instance_url="https://superset.test")


class QueryContextlessClient(Client):
    """Charts loaded by load_examples or dashboard import carry no query_context."""

    def request(self, method, path, **kwargs):
        if path.endswith("/charts"):
            return {"result": [{"id": 11, "slice_name": "Revenue", "form_data": {
                "datasource": "9__table", "viz_type": "pie",
                "groupby": ["region"], "metric": "count", "row_limit": 500}}]}
        if path == "/api/v1/chart/11":
            return {"result": {"id": 11, "slice_name": "Revenue"}}
        if path == "/api/v1/chart/data":
            body = kwargs["json_body"]
            assert body["datasource"] == {"id": 9, "type": "table"}
            assert body["queries"][0]["columns"] == ["region"]
            assert body["queries"][0]["metrics"] == ["count"]
            assert body["queries"][0]["row_limit"] == 500
            return {"result": [{"query": "SELECT region, count(*) FROM sales GROUP BY region"}]}
        return super().request(method, path, **kwargs)


class SourceIdentityClient(QueryContextlessClient):
    def request(self, method, path, **kwargs):
        if path == "/api/v1/dataset/9":
            return {
                "result": {
                    "id": 9,
                    "table_name": "sales",
                    "schema": "public",
                    "database": {"id": 3, "database_name": "Examples", "backend": "postgresql"},
                }
            }
        if path == "/api/v1/database/3":
            return {"result": {"id": 3, "database_name": "Examples", "backend": "postgresql"}}
        if path == "/api/v1/database/3/connection":
            return {
                "result": {
                    "sqlalchemy_uri": "postgresql+psycopg2://reader:secret@postgres:5432/superset_examples"
                }
            }
        return super().request(method, path, **kwargs)


def test_export_falls_back_to_form_data_when_query_context_is_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = export_dashboard(QueryContextlessClient(), "7", instance_url="https://superset.test")
    assert result["succeeded"] == 1 and result["failed"] == 0
    directory = Path(result["output_dir"])
    assert "FROM sales" in next(directory.glob("*.sql")).read_text()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["queries"][0]["datasource"] == "9__table"


def test_export_resolves_query_level_source_identity_without_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = export_dashboard(SourceIdentityClient(), "7", instance_url="https://superset.test")
    manifest_text = (Path(result["output_dir"]) / "manifest.json").read_text()
    manifest = json.loads(manifest_text)

    assert "serving_datasource" not in manifest
    source = manifest["queries"][0]["source_identity"]
    assert source == {
        "provider": "superset",
        "status": "resolved",
        "datasource": {"id": 9, "type": "table"},
        "dataset": {"id": 9, "name": "sales", "schema": "public", "type": "table"},
        "database": {"id": 3, "name": "Examples", "backend": "postgresql"},
        "connection": {
            "backend": "postgresql",
            "driver": "psycopg2",
            "host": "postgres",
            "port": 5432,
            "database": "superset_examples",
        },
    }
    assert "reader" not in manifest_text
    assert "secret" not in manifest_text
    assert "sqlalchemy_uri" not in manifest_text


def test_candidate_discovery_exposes_source_identity_without_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = discover_dashboard_candidates(SourceIdentityClient(), "7")

    assert result["dashboard"] == {"id": 7, "name": "Revenue Overview"}
    assert result["candidates"] == [
        {
            "id": "chart-11",
            "name": "Revenue",
            "hidden": False,
            "exportable": True,
            "source_identity": {
                "provider": "superset",
                "status": "resolved",
                "datasource": {"id": 9, "type": "table"},
                "dataset": {"id": 9, "name": "sales", "schema": "public", "type": "table"},
                "database": {"id": 3, "name": "Examples", "backend": "postgresql"},
                "connection": {
                    "backend": "postgresql",
                    "driver": "psycopg2",
                    "host": "postgres",
                    "port": 5432,
                    "database": "superset_examples",
                },
            },
            "plugin_metadata": {"asset_type": "chart", "asset_id": 11},
        }
    ]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("form_data", [
    None,
    {},
    {"datasource": "None__table", "groupby": ["x"]},   # chart lost its dataset
    {"datasource": "9__table"},                        # nothing to select
])
def test_synthesized_context_declines_unusable_form_data(form_data):
    assert _synthesized_context(form_data) is None


def test_synthesized_context_merges_dimension_keys_across_viz_types():
    context = _synthesized_context({
        "datasource": "9__table", "groupbyColumns": ["state"], "groupbyRows": ["name"],
        "groupby": ["state"], "metrics": ["sum__num"],
    })
    assert context["datasource"] == {"id": 9, "type": "table"}
    assert context["queries"][0]["columns"] == ["state", "name"]


def test_variables_are_names_not_regex_pairs():
    assert _variables({"x": "$region ${tenant:sqlstring}"}) == ["region", "tenant"]


def test_output_cannot_escape_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageError):
        export_dashboard(Client(), "7", output_root="../escape", instance_url="https://superset.test")
