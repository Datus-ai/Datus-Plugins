from __future__ import annotations

import json
from pathlib import Path

import pytest

from datus_superset_plugin.errors import UsageError
from datus_superset_plugin.exporter import _chart_sql, _variables, export_dashboard
from datus_superset_plugin.query_context import build_query_context


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
        if path == "/api/v1/explore/":
            return {"result": {}}
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


class FormDataClient:
    def __init__(self):
        self.query_context = None

    def request(self, method, path, **kwargs):
        if path == "/api/v1/dashboard/7":
            return {"result": {"id": 7, "dashboard_title": "Population"}}
        if path.endswith("/charts"):
            return {
                "result": [
                    {
                        "id": 11,
                        "slice_name": "Population by Country",
                        "form_data": {
                            "datasource": "1__table",
                            "viz_type": "table",
                            "groupby": ["country_name"],
                            "metrics": ["sum__population"],
                            "row_limit": 100,
                        },
                    }
                ]
            }
        if path == "/api/v1/chart/11":
            return {
                "result": {
                    "id": 11,
                    "slice_name": "Population by Country",
                    "query_context": None,
                }
            }
        if path == "/api/v1/chart/data":
            self.query_context = kwargs["json_body"]
            return {"result": [{"query": "SELECT country_name, SUM(population) FROM world_bank"}]}
        raise AssertionError(path)


def test_export_rebuilds_missing_query_context_from_dashboard_form_data(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    client = FormDataClient()

    result = export_dashboard(
        client,
        "7",
        instance_url="https://superset.test",
    )

    assert result["succeeded"] == 1
    assert client.query_context["datasource"] == {"id": 1, "type": "table"}
    assert client.query_context["result_type"] == "query"
    manifest = json.loads(
        (Path(result["output_dir"]) / "manifest.json").read_text()
    )
    assert manifest["queries"][0]["datasource"] == "1__table"


def test_chart_sql_uses_explore_form_data_when_chart_payload_is_incomplete():
    class ExploreClient:
        def request(self, method, path, **kwargs):
            if path == "/api/v1/explore/":
                assert kwargs["params"] == {"slice_id": 11}
                return {
                    "result": {
                        "form_data": {
                            "datasource": "1__table",
                            "viz_type": "big_number",
                            "metric": "sum__population",
                        }
                    }
                }
            if path == "/api/v1/chart/data":
                return {"result": [{"query": "SELECT SUM(population) FROM world_bank"}]}
            raise AssertionError(path)

    assert _chart_sql(ExploreClient(), 11, {"query_context": None}) == [
        "SELECT SUM(population) FROM world_bank"
    ]


@pytest.mark.parametrize(
    ("viz_type", "fields"),
    [
        ("big_number", {"metric": "sum__SP_POP_TOTL"}),
        (
            "table",
            {"metrics": ["sum__SP_POP_TOTL"], "groupby": ["country_name"]},
        ),
        (
            "line",
            {
                "metrics": ["sum__SP_POP_TOTL"],
                "groupby": ["country_name"],
                "granularity_sqla": "year",
            },
        ),
        (
            "world_map",
            {
                "metric": "sum__SP_RUR_TOTL_ZS",
                "entity": "country_code",
                "country_fieldtype": "cca3",
            },
        ),
        (
            "bubble",
            {
                "x": "sum__SP_RUR_TOTL_ZS",
                "y": "sum__SP_DYN_LE00_IN",
                "size": "sum__SP_POP_TOTL",
                "entity": "country_name",
            },
        ),
        (
            "sunburst_v2",
            {
                "metric": "sum__SP_POP_TOTL",
                "groupby": ["region", "country_name"],
            },
        ),
        (
            "area",
            {
                "metrics": ["sum__SP_POP_TOTL"],
                "groupby": ["country_name"],
                "granularity_sqla": "year",
            },
        ),
        (
            "box_plot",
            {"metrics": ["sum__SP_POP_TOTL"], "groupby": ["region"]},
        ),
        (
            "treemap_v2",
            {
                "metric": "sum__SP_POP_TOTL",
                "groupby": ["region", "country_name"],
            },
        ),
    ],
)
def test_world_bank_chart_types_build_query_context(viz_type, fields):
    context = build_query_context(
        {
            "datasource": "1__table",
            "viz_type": viz_type,
            "time_range": "No filter",
            **fields,
        }
    )

    assert context["datasource"] == {"id": 1, "type": "table"}
    assert context["queries"]
