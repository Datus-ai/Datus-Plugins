from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from datus_tableau_plugin.bundle import inspect_bundle
from datus_tableau_plugin.native import parse_datasource, parse_workbook
from datus_tableau_plugin.sync import sync_sources
from datus_tableau_plugin.transforms import generate_transforms
from test_sync import Client, assess_synced_bundle, workbook
from test_transforms import model, node


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def credential_payload(kind):
    datasource = """<datasource name="source" xmlns:a="urn:auth"><connection class="postgres" server="warehouse.example.test" dbname="analytics">
    <a:connection-string value="synthetic-self-attr">synthetic-self-text<value a:opaque="synthetic-child-attr">synthetic-child-text<nested other="synthetic-deep-attr">synthetic-deep-text</nested>synthetic-deep-tail</value>synthetic-child-tail</a:connection-string>ordinary-parent-tail<relation type="text">SELECT 7 AS revenue</relation>
    </connection><filter column="region" value="East"/></datasource>"""
    if kind == "datasource":
        return datasource.encode()
    return workbook("book1").replace(
        workbook("book1").split(b"<datasources>")[1].split(b"</datasources>")[0],
        datasource.encode(),
    )


@pytest.mark.parametrize("kind", ["workbook", "datasource"])
def test_sensitive_xml_subtree_redacts_all_values(kind):
    payload = credential_payload(kind)
    parsed = (
        parse_workbook(payload, workbook_id="book1")
        if kind == "workbook"
        else parse_datasource(payload, datasource_id="source")
    )
    rendered = json.dumps(parsed)
    assert "synthetic-" not in rendered
    assert "warehouse.example.test" in rendered and "analytics" in rendered
    assert "East" in rendered and "SELECT 7 AS revenue" in rendered
    assert "ordinary-parent-tail" in rendered
    document = next(iter(parsed["documents"].values()))
    assert "connection-string" in document and "value=" in document
    assert "opaque=" in document and "nested" in document and "[REDACTED]" in document


def test_sensitive_xml_subtree_cannot_leak_into_bundle(workspace):
    client = Client()
    client.download = lambda group, identifier: credential_payload("workbook")
    result = sync_sources(client, all_dashboards=True)
    inspected = inspect_bundle(result["output_dir"])
    contents = b"\n".join(
        (Path(result["output_dir"]) / name).read_bytes()
        for name in inspected["manifest"]["files"]
    )
    assert b"synthetic-" not in contents
    assert b"SELECT 7 AS revenue" in contents and b"warehouse.example.test" in contents
    assert b"East" in contents


def cycle_assets():
    return [
        node("a", {"type": "clean", "changes": []}, ("b", "upstream")),
        node("b", {"type": "clean", "changes": []}, ("a",)),
        node("descendant", {"type": "output"}, ("b",)),
        node("upstream", {"type": "source", "sql": "SELECT 7 AS value"}),
        node("independent", {"type": "source", "sql": "SELECT 9 AS value"}),
        node("valid-output", {"type": "output"}, ("independent",)),
    ]


def test_cycle_only_blocks_members_and_downstream_models():
    result = generate_transforms(cycle_assets(), [], dialect="sqlite")
    rows = {row["id"]: row for row in result["pipeline"]["nodes"]}
    assert {name for name, row in rows.items() if row["status"] == "blocked"} == {
        "a",
        "b",
        "descendant",
    }
    assert result["pipeline"]["cycle"] and not result["validation"]["checks"]["acyclic"]
    assert (
        result["validation"]["generated"] == 3 and result["validation"]["blocked"] == 3
    )
    with sqlite3.connect(":memory:") as database:
        assert database.execute(model(result, "upstream")).fetchall() == [(7,)]
        assert database.execute(model(result, "valid-output")).fetchall() == [(9,)]


def test_sync_cycle_coverage_and_rebuild_are_scoped_to_affected_charts(workspace):
    assets = cycle_assets()
    edges = [
        {"from": "chart:book1:Revenue", "to": "descendant", "relation": "depends_on"},
        {"from": "chart:book1:Trend", "to": "valid-output", "relation": "depends_on"},
    ]
    edges.extend(
        {"from": asset["id"], "to": dependency, "relation": "depends_on"}
        for asset in assets
        for dependency in asset["definition"]["inputs"]
    )
    pack = workspace / "cycle.json"
    pack.write_text(json.dumps({"assets": assets, "dependencies": edges}))
    result = sync_sources(
        Client(), all_dashboards=True, external_sources=[str(pack)], dialect="sqlite"
    )
    inspected, report = assess_synced_bundle(result["output_dir"])
    charts = {row["chart_id"]: row for row in report["charts"]}
    assert report["summary"] == {"total": 2, "ready": 1, "blocked": 1}
    assert charts["chart:book1:Trend"]["status"] == "ready_for_metric_validation"
    assert any(
        "processing_model_blocked" in reason
        for reason in charts["chart:book1:Revenue"]["reasons"]
    )
    cycle_ids = {
        row["asset_id"]
        for row in inspected["coverage"]
        if row["code"] == "dependency_cycle"
    }
    assert cycle_ids and cycle_ids <= {"a", "b"}
    blocked_ids = {
        row["asset_id"]
        for row in inspected["coverage"]
        if row["code"] == "processing_model_blocked"
    }
    assert blocked_ids == {"a", "b", "descendant"}
    assert not cycle_ids & {
        "upstream",
        "independent",
        "valid-output",
        "chart:book1:Trend",
    }
    pipeline = json.loads((Path(result["output_dir"]) / "pipeline.json").read_text())
    assert pipeline["cycle"]
    row = next(row for row in pipeline["nodes"] if row["id"] == "valid-output")
    with sqlite3.connect(":memory:") as database:
        assert database.execute(
            (Path(result["output_dir"]) / row["file"]).read_text()
        ).fetchall() == [(9,)]
