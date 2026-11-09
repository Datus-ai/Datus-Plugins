from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3

import pytest

from datus_tableau_plugin.native import parse_datasource, parse_workbook
from datus_tableau_plugin.sync import sync_sources
from datus_tableau_plugin.bundle import inspect_bundle
from datus_tableau_plugin.rebuild import assess_rebuild
from datus_tableau_plugin.transforms import generate_transforms
from test_sync import Client, workbook, assess_synced_bundle
from test_transforms import node, model
from test_rebuild_workflow import binding, catalog


@pytest.mark.parametrize("kind", ["workbook", "datasource"])
def test_namespaced_credentials_do_not_override_attribute_identity(kind):
    datasource = """<datasource name="sales" xmlns:auth="urn:auth"><connection
      class="postgres" server="warehouse.example.test" dbname="analytics"
      password="plain-credential" auth:password="namespaced-credential"
      auth:connection-string="PWD=namespaced-odbc-secret"/>
    </datasource>"""
    parsed = (
        parse_datasource(datasource.encode(), datasource_id="source")
        if kind == "datasource"
        else parse_workbook(
            f"<workbook><datasources>{datasource}</datasources></workbook>".encode(),
            workbook_id="book",
        )
    )
    rendered = json.dumps(parsed)
    assert "plain-credential" not in rendered
    assert "namespaced-credential" not in rendered
    assert "namespaced-odbc-secret" not in rendered
    asset = next(a for a in parsed["assets"] if a["kind"] == "dataset")
    connection = asset["definition"]["connections"][0]
    assert connection["password"] == "[REDACTED]"
    assert connection["{urn:auth}password"] == "[REDACTED]"
    assert connection["server"] == "warehouse.example.test"
    assert connection["dbname"] == "analytics"


@pytest.mark.parametrize("kind", ["workbook", "datasource"])
def test_sample_child_subtrees_are_removed_without_losing_filters_or_layout(kind):
    datasource = """<datasource name="sales" xmlns:p="urn:preview"><connection class="postgres" server="warehouse.example.test">
      <sample-data><row>sample-business-row</row></sample-data>
      <p:previewData><p:row>preview-business-row</p:row></p:previewData>
      <relation type="text">SELECT 7 AS revenue</relation>
    </connection><filter column="region" value="East"/></datasource>"""
    payload = datasource
    if kind == "workbook":
        payload = f'<workbook><datasources>{datasource}</datasources><dashboards><dashboard name="Overview"><zones><zone name="Selected"/></zones></dashboard></dashboards></workbook>'
    parsed = (
        parse_datasource(payload.encode(), datasource_id="source")
        if kind == "datasource"
        else parse_workbook(payload.encode(), workbook_id="book")
    )
    rendered = json.dumps(parsed)
    assert "sample-business-row" not in rendered
    assert "preview-business-row" not in rendered
    assert "East" in rendered and "warehouse.example.test" in rendered
    assert parsed["queries"][0]["text"] == "SELECT 7 AS revenue"
    assert b"SELECT 7" in next(iter(parsed["documents"].values())).encode()


@pytest.mark.parametrize("changes", [None, False, 0, "", {}, ["untranslated"]])
def test_clean_requires_explicit_empty_changes_and_preserves_independent_branch(
    changes,
):
    result = generate_transforms(
        [
            node("source", {"type": "source", "table": "orders"}),
            node("clean", {"type": "clean", "changes": changes}, ("source",)),
            node("output", {"type": "output"}, ("clean",)),
            node("independent", {"type": "source", "sql": "SELECT 7 AS value"}),
        ],
        [],
        dialect="sqlite",
    )
    rows = {row["id"]: row for row in result["pipeline"]["nodes"]}
    assert rows["clean"]["status"] == "blocked"
    assert rows["output"]["status"] == "blocked"
    assert rows["independent"]["status"] == "generated"
    with sqlite3.connect(":memory:") as database:
        assert database.execute(model(result, "independent")).fetchall() == [(7,)]


def test_missing_clean_changes_is_not_verified_passthrough():
    result = generate_transforms(
        [
            node("source", {"type": "source", "table": "orders"}),
            node("clean", {"type": "clean"}, ("source",)),
        ],
        [],
    )
    assert (
        next(row for row in result["pipeline"]["nodes"] if row["id"] == "clean")[
            "status"
        ]
        == "blocked"
    )


def test_explicit_empty_clean_changes_remains_supported():
    result = generate_transforms(
        [
            node("source", {"type": "source", "sql": "SELECT 7 AS value"}),
            node("clean", {"type": "clean", "changes": []}, ("source",)),
        ],
        [],
        dialect="sqlite",
    )
    assert result["validation"]["blocked"] == 0
    with sqlite3.connect(":memory:") as database:
        assert database.execute(model(result, "clean")).fetchall() == [(7,)]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_metadata_table_gap_is_visible_to_selected_consumers_only(workspace):
    client = Client(books=2)
    client.workbook_lineage = lambda identifier: {
        "workbook": {"id": "metadata:" + identifier, "luid": identifier},
        "datasources": [],
        "flows": [],
        "tables": [{"id": "table-one", "name": "sales", "fullName": "public.sales"}]
        if identifier == "book1"
        else [],
    }
    result = sync_sources(client, all_dashboards=True)
    bundle, report = assess_synced_bundle(result["output_dir"])
    charts = {row["chart_id"]: row for row in report["charts"]}
    assert report["summary"] == {"total": 4, "ready": 2, "blocked": 2}
    assert any(
        "external_definition_required" in reason
        for reason in charts["chart:book1:Revenue"]["reasons"]
    )
    assert charts["chart:book2:Revenue"]["status"] == "ready_for_metric_validation"
    assert charts["chart:book2:Trend"]["status"] == "ready_for_metric_validation"
    assert {
        edge["from"]
        for edge in bundle["dependencies"]
        if edge["to"] == "external_source:table:table-one"
    } == {"workbook:book1", "chart:book1:Revenue", "chart:book1:Trend"}
    manifest_hash = hashlib.sha256(
        (Path(result["output_dir"]) / "manifest.json").read_bytes()
    ).hexdigest()
    bindings = {
        "schema_version": 1,
        "source_manifest_sha256": manifest_hash,
        "charts": [],
    }
    for index, asset in enumerate(a for a in bundle["assets"] if a["kind"] == "chart"):
        item = binding()["charts"][0]
        item["chart_id"] = asset["id"]
        item["queries"][0]["name"] = f"metric_query_{index}"
        if asset["id"] == "chart:book1:Revenue":
            item["source_gap_resolutions"] = [
                {
                    "asset_id": "external_source:table:table-one",
                    "code": "external_definition_required",
                    "resolution": "existing_dosi_target",
                    "evidence": "The discovered target metric has independently verified source tables.",
                    "metric_bindings": [
                        {
                            key: catalog()[0][key]
                            for key in ("metric", "datasource", "model_revision")
                        }
                    ],
                }
            ]
        bindings["charts"].append(item)
    resolved = assess_rebuild(
        bundle,
        bindings=bindings,
        metric_catalog=catalog(),
        source_manifest_sha256=manifest_hash,
    )
    assert resolved["summary"] == {"total": 4, "ready": 3, "blocked": 1}


def test_partial_selection_does_not_export_unselected_native_or_metadata_sql(workspace):
    client = Client()
    payload = (
        workbook("book1")
        .replace(
            b"</datasources>",
            b"""<datasource name="private"><connection><relation type="text">SELECT 'unselected-sql-sentinel'</relation></connection></datasource></datasources>""",
            1,
        )
        .replace(
            b'<worksheet name="Trend"><table><view><datasources><datasource name="source"/>',
            b'<worksheet name="Trend"><table><view><datasources><datasource name="private"/>',
        )
    )
    client.download = lambda group, identifier: payload
    client.workbook_custom_sql = lambda identifier: [
        {
            "id": "unselected-query",
            "name": "Private SQL",
            "query": "SELECT 'unselected-metadata-sql-sentinel'",
            "isUnsupportedCustomSql": False,
        }
    ]
    result = sync_sources(client, dashboard_ids=["dashboard:book1:Overview"])
    bundle = inspect_bundle(result["output_dir"])
    contents = b"\n".join(
        (Path(result["output_dir"]) / name).read_bytes()
        for name in bundle["manifest"]["files"]
    )
    assert b"unselected-sql-sentinel" not in contents
    assert b"unselected-metadata-sql-sentinel" not in contents
    assert b"SUM(amount)" in contents
    assert "chart:book1:Revenue" in {asset["id"] for asset in bundle["assets"]}
    assert not any(
        name.startswith("native/") or name.startswith("metadata/")
        for name in bundle["manifest"]["files"]
    )
    assert any(c["code"] == "selection_scoped_source_copy" for c in bundle["coverage"])


def test_native_sensitive_nodes_and_namespaced_credentials_do_not_reach_bundle(
    workspace,
):
    client = Client()
    payload = workbook("book1").replace(
        b'<connection class="postgres" server="warehouse.example.test" dbname="analytics">',
        b"""<connection class="postgres" server="warehouse.example.test" dbname="analytics" xmlns:a="urn:auth" a:password="bundle-namespaced-secret"><sample-data><row>bundle-sample-row</row></sample-data><a:previewData><a:row>bundle-preview-row</a:row></a:previewData>""",
        1,
    )
    client.download = lambda group, identifier: payload
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    contents = b"\n".join(
        (Path(result["output_dir"]) / name).read_bytes()
        for name in bundle["manifest"]["files"]
    )
    assert b"bundle-namespaced-secret" not in contents
    assert b"bundle-sample-row" not in contents
    assert b"bundle-preview-row" not in contents
    assert b"warehouse.example.test" in contents
    assert b"SUM(amount)" in contents


def test_deep_source_graph_is_inspectable_and_only_its_consuming_chart_is_blocked(
    workspace,
):
    transforms = [node("transform:chain:0", {"type": "source", "table": "orders"})]
    edges = []
    for index in range(1, 1500):
        identifier = f"transform:chain:{index}"
        previous = f"transform:chain:{index - 1}"
        transforms.append(node(identifier, {"type": "output"}, (previous,)))
        edges.append({"from": identifier, "to": previous, "relation": "depends_on"})
    transforms.append(
        node("transform:z-independent", {"type": "source", "sql": "SELECT 7 AS value"})
    )
    edges.append(
        {
            "from": "chart:book1:Revenue",
            "to": "transform:chain:1499",
            "relation": "depends_on",
        }
    )
    pack = workspace / "deep-graph.json"
    pack.write_text(json.dumps({"assets": transforms, "dependencies": edges}))
    result = sync_sources(
        Client(), all_dashboards=True, external_sources=[str(pack)], dialect="sqlite"
    )
    bundle, report = assess_synced_bundle(result["output_dir"])
    pipeline = json.loads((Path(result["output_dir"]) / "pipeline.json").read_text())
    rows = {row["id"]: row for row in pipeline["nodes"]}
    assert len(rows) == 1501
    assert rows["transform:chain:1499"]["status"] == "blocked"
    assert any(
        "ancestor resource limit" in (row.get("error") or "") for row in rows.values()
    )
    assert rows["transform:z-independent"]["status"] == "generated"
    assert not pipeline["cycle"]
    assert pipeline["limits"]["model_ancestors"] == 64
    with sqlite3.connect(":memory:") as database:
        sql = (
            Path(result["output_dir"]) / rows["transform:z-independent"]["file"]
        ).read_text()
        assert database.execute(sql).fetchall() == [(7,)]
    assert len(bundle["assets"]) >= 1501
    charts = {row["chart_id"]: row for row in report["charts"]}
    assert charts["chart:book1:Revenue"]["status"] == "blocked"
    assert charts["chart:book1:Trend"]["status"] == "ready_for_metric_validation"
    assert report["summary"] == {"total": 2, "ready": 1, "blocked": 1}


def test_rebuild_deep_dataset_chain_returns_resource_blocker_instead_of_recursion_error():
    from test_rebuild_workflow import source, assess

    bundle = source()
    for index in range(1500):
        identifier = f"dataset:depth:{index}"
        bundle["assets"].append(
            {
                "id": identifier,
                "kind": "dataset",
                "name": identifier,
                "source": {},
                "definition": {},
                "status": "ok",
            }
        )
        previous = "dataset:shared" if index == 0 else f"dataset:depth:{index - 1}"
        bundle["dependencies"].append(
            {"from": previous, "to": identifier, "relation": "reads"}
        )
    report = assess(doc=bundle)
    assert report["summary"] == {"total": 1, "ready": 0, "blocked": 1}
    assert any(
        "dependency traversal resource limit" in reason
        for reason in report["charts"][0]["reasons"]
    )


def test_model_byte_limits_block_expansion_and_keep_an_independent_small_model():
    source_sql = "SELECT '" + "x" * (200 * 1024) + "' AS payload"
    assets = [node("chain:0", {"type": "source", "sql": source_sql})]
    for index in range(1, 60):
        assets.append(
            node(f"chain:{index}", {"type": "output"}, (f"chain:{index - 1}",))
        )
    assets.append(node("z-independent", {"type": "source", "sql": "SELECT 7 AS value"}))
    result = generate_transforms(assets, [], dialect="sqlite")
    rows = {row["id"]: row for row in result["pipeline"]["nodes"]}
    assert any(
        "SQL byte resource limit" in (row.get("error") or "") for row in rows.values()
    )
    assert rows["chain:59"]["status"] == "blocked"
    assert rows["z-independent"]["status"] == "generated"
    assert (
        sum(len(sql.encode()) for sql in result["models"].values()) <= 8 * 1024 * 1024
    )
    assert max(len(sql.encode()) for sql in result["models"].values()) <= 256 * 1024
    with sqlite3.connect(":memory:") as database:
        assert database.execute(model(result, "z-independent")).fetchall() == [(7,)]
