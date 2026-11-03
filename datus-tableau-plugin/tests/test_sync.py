from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from datus_tableau_plugin.bundle import inspect_bundle
from datus_tableau_plugin.errors import PluginError, UsageError
from datus_tableau_plugin.native import parse_workbook
from datus_tableau_plugin.sync import sync_sources


def workbook(identifier, *, published=False, physical=False):
    relation = (
        '<relation type="table" table="[sales]"/>'
        if physical
        else '<relation type="text">SELECT SUM(amount) AS revenue FROM sales</relation>'
    )
    connection = (
        '<repository-location id="pub-luid"/>'
        if published
        else f'<connection class="postgres" server="warehouse.example.test" dbname="analytics">{relation}</connection>'
    )
    return f"""<workbook version="2023.3"><datasources><datasource name="source" caption="Sales">{connection}</datasource></datasources>
    <worksheets><worksheet name="Revenue"><table><view><datasources><datasource name="source"/></datasources></view><rows>[source].[revenue]</rows></table></worksheet>
    <worksheet name="Trend"><table><view><datasources><datasource name="source"/></datasources></view></table></worksheet></worksheets>
    <dashboards><dashboard name="Overview"><zones><zone name="Revenue"/></zones></dashboard><dashboard name="Other"><zones><zone name="Trend"/></zones></dashboard></dashboards></workbook>""".encode()


class Client:
    settings = SimpleNamespace(
        server_url="https://tableau.example.test", site_content_url="sales"
    )

    def __init__(self, *, published=False, physical=False, books=1):
        self.published = published
        self.physical = physical
        self.books = books
        self.calls = Counter()
        self.failed = set()

    def list_resources(self, group, **kw):
        return [
            {
                "id": f"book{i}",
                "name": f"Book {i}",
                "project_id": "sales" if i == 1 else "finance",
            }
            for i in range(1, self.books + 1)
        ]

    def get_resource(self, group, identifier):
        return {"id": identifier, "content_url": "published-sales", "connections": []}

    def download(self, group, identifier):
        self.calls[(group, identifier)] += 1
        if (group, identifier) in self.failed:
            raise RuntimeError("source secret must not surface")
        if group == "workbooks":
            return workbook(
                identifier, published=self.published, physical=self.physical
            )
        if group == "datasources":
            return b'<datasource name="published"><connection class="postgres"><relation type="text">SELECT 7 AS revenue</relation></connection></datasource>'
        raise AssertionError(group)

    def workbook_lineage(self, identifier):
        if ("metadata", identifier) in self.failed:
            raise RuntimeError("GraphQL error")
        return {
            "workbook": {"id": "metadata:" + identifier, "luid": identifier},
            "tables": [],
            "flows": [],
            "datasources": [
                {"id": "metadata:pub", "luid": "pub-luid", "name": "Published Sales"}
            ]
            if self.published
            else [],
        }

    def workbook_custom_sql(self, identifier):
        return []


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_partial_selection_excludes_siblings_and_unrelated_workbooks(workspace):
    client = Client(books=2)
    client.failed.add(("workbooks", "book2"))
    result = sync_sources(client, dashboard_ids=["dashboard:book1:Overview"])
    bundle = inspect_bundle(result["output_dir"])
    ids = {asset["id"] for asset in bundle["assets"]}
    assert "dashboard:book1:Overview" in ids
    assert "chart:book1:Revenue" in ids
    assert "dashboard:book1:Other" not in ids
    assert "chart:book1:Trend" not in ids
    assert client.calls[("workbooks", "book2")] == 0
    assert bundle["queries"][0]["origin"] == "source"
    assert (
        "SUM(amount)"
        in (Path(result["output_dir"]) / bundle["queries"][0]["file"]).read_text()
    )
    assert result["status"] == "ok"
    assert set(bundle["manifest"]["files"]) >= {
        "assets.json",
        "queries.json",
        "dependencies.json",
        "coverage.json",
        "pipeline.json",
        "validation.json",
    }


def test_shared_published_dataset_downloaded_once_and_linked_to_each_chart(workspace):
    client = Client(published=True, books=2)
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    assert client.calls[("datasources", "pub-luid")] == 1
    assert len([a for a in bundle["assets"] if a["id"] == "dataset:pub-luid"]) == 1
    for book in ("book1", "book2"):
        assert {
            "from": f"dataset:{book}:source",
            "to": "dataset:pub-luid",
            "relation": "published_datasource",
        } in bundle["dependencies"]
    assert not [
        c for c in bundle["coverage"] if c["code"] == "lineage_binding_unresolved"
    ]


def test_metadata_failure_is_attached_to_consuming_chart(workspace):
    client = Client()
    client.failed.add(("metadata", "book1"))
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    assert result["status"] == "partial"
    assert result["summary"]["failures"] == 1
    assert any(
        c["asset_id"] == "chart:book1:Revenue" and c["code"] == "metadata_unavailable"
        for c in bundle["coverage"]
    )


def test_failed_update_preserves_previous_snapshot_and_explicit_overwrite(workspace):
    client = Client()
    result = sync_sources(client, all_dashboards=True)
    root = Path(result["output_dir"])
    previous = (root / "manifest.json").read_bytes()
    with pytest.raises(UsageError, match="overwrite"):
        sync_sources(client, all_dashboards=True)
    client.failed.add(("metadata", "book1"))
    with pytest.raises(PluginError, match="preserved"):
        sync_sources(client, all_dashboards=True, overwrite=True)
    assert (root / "manifest.json").read_bytes() == previous
    client.failed.clear()
    updated = sync_sources(client, all_dashboards=True, overwrite=True)
    assert updated["status"] == "ok"


def test_full_selection_retains_unreadable_workbook_as_error_not_fake_dashboard(
    workspace,
):
    client = Client(books=2)
    client.failed.add(("workbooks", "book2"))
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    failed = next(a for a in bundle["assets"] if a["id"] == "workbook:book2")
    assert failed["status"] == "error"
    assert not any(
        a["kind"] == "dashboard" and a["source"].get("workbook_luid") == "book2"
        for a in bundle["assets"]
    )
    assert (
        "source secret"
        not in (Path(result["output_dir"]) / "coverage.json").read_text()
    )


def test_project_selection_downloads_only_that_project(workspace):
    client = Client(books=2)
    result = sync_sources(client, project_ids=["finance"])
    assert client.calls[("workbooks", "book1")] == 0
    assert {
        a["id"]
        for a in inspect_bundle(result["output_dir"])["assets"]
        if a["kind"] == "dashboard"
    } == {"dashboard:book2:Overview", "dashboard:book2:Other"}


def test_metadata_custom_sql_is_separately_marked_as_source(workspace):
    client = Client()
    client.workbook_custom_sql = lambda identifier: [
        {
            "id": "opaque-query",
            "name": "Additional query",
            "query": "SELECT 123 AS exact_metadata_sql",
            "isUnsupportedCustomSql": True,
        }
    ]
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    query = next(
        q for q in bundle["queries"] if q["id"] == "query:metadata:opaque-query"
    )
    assert query["origin"] == "source" and query["status"] == "partial"
    assert (
        Path(result["output_dir"]) / query["file"]
    ).read_text().strip() == "SELECT 123 AS exact_metadata_sql"


def test_external_pack_can_resolve_known_base_table_boundary(workspace):
    client = Client(physical=True)
    document = parse_workbook(workbook("book1", physical=True), workbook_id="book1")
    external = copy.deepcopy(
        next(a for a in document["assets"] if a["kind"] == "external_source")
    )
    external["definition"]["base_source"] = True
    external["status"] = "ok"
    pack = workspace / "base-table.json"
    pack.write_text(json.dumps({"assets": [external]}))
    result = sync_sources(client, all_dashboards=True, external_sources=[str(pack)])
    bundle = inspect_bundle(result["output_dir"])
    assert not any(
        c["asset_id"] == external["id"] and c["code"] == "external_etl_unresolved"
        for c in bundle["coverage"]
    )
    assert (
        next(a for a in bundle["assets"] if a["id"] == external["id"])["definition"][
            "base_source"
        ]
        is True
    )


def test_bundle_tampering_and_symlinks_are_rejected(workspace):
    result = sync_sources(Client(), all_dashboards=True)
    root = Path(result["output_dir"])
    link = workspace / "symlink"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(UsageError, match="symbolic"):
        inspect_bundle(link)
    assets = root / "assets.json"
    original = assets.read_bytes()
    assets.write_bytes(original + b" ")
    with pytest.raises(PluginError, match="checksum"):
        inspect_bundle(root)
    assets.write_bytes(original)
    manifest = root / "manifest.json"
    stored = manifest.read_text()
    outside = workspace / "outside.json"
    outside.write_text(stored)
    manifest.unlink()
    manifest.symlink_to(outside)
    with pytest.raises(PluginError, match="symbolic"):
        inspect_bundle(root)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"all_dashboards": True, "project_ids": ["sales"]},
        {"dashboard_ids": ["not-a-scoped-id"]},
    ],
)
def test_invalid_selection(workspace, kwargs):
    with pytest.raises(UsageError):
        sync_sources(Client(), **kwargs)


def test_shared_metadata_query_identity_is_not_conflicted_by_workbook(workspace):
    client = Client(books=2)
    client.workbook_custom_sql = lambda identifier: [
        {
            "id": "shared-query",
            "name": "Shared SQL",
            "query": "SELECT 10",
            "isUnsupportedCustomSql": False,
        }
    ]
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    assert result["summary"]["failures"] == 0
    assert (
        len(
            [
                query
                for query in bundle["queries"]
                if query["id"] == "query:metadata:shared-query"
            ]
        )
        == 1
    )
    assert {
        edge["from"]
        for edge in bundle["dependencies"]
        if edge["to"] == "query:metadata:shared-query"
    } == {"workbook:book1", "workbook:book2"}


def test_unresolved_upstream_flow_and_blocked_generation_reach_consumers(workspace):
    client = Client()
    original_download = client.download

    def download(group, identifier):
        if group == "flows":
            return json.dumps(
                {
                    "flow": {
                        "name": "Native calculation",
                        "nodes": {"opaque": {"nodeType": "vendor.CustomPythonScript"}},
                    }
                }
            ).encode()
        return original_download(group, identifier)

    client.download = download
    client.workbook_lineage = lambda identifier: {
        "workbook": {"id": "metadata:book", "luid": identifier},
        "tables": [],
        "datasources": [],
        "flows": [{"id": "metadata:flow", "luid": "flow-luid", "name": "Prep"}],
    }
    result = sync_sources(client, dashboard_ids=["dashboard:book1:Overview"])
    bundle = inspect_bundle(result["output_dir"])
    assert result["status"] == "partial"
    assert "transform:flow-luid" in {a["id"] for a in bundle["assets"]}
    assert "flow:flow-luid" not in {a["id"] for a in bundle["assets"]}
    assert any(
        edge["from"] == "chart:book1:Revenue" and edge["to"] == "transform:flow-luid"
        for edge in bundle["dependencies"]
    )
    assert any(
        row["code"] == "lineage_binding_unresolved"
        and row["asset_id"] == "chart:book1:Revenue"
        for row in bundle["coverage"]
    )
    assert any(row["code"] == "processing_model_blocked" for row in bundle["coverage"])
    pipeline = json.loads((Path(result["output_dir"]) / "pipeline.json").read_text())
    assert pipeline["execution"] == "not_run"
    assert pipeline["status"] == "partial"
