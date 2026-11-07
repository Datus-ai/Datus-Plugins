"""Published references and extract caches are not physical upstream tables."""

from pathlib import Path

import pytest

from datus_tableau_plugin.bundle import inspect_bundle
from datus_tableau_plugin.native import parse_datasource
from datus_tableau_plugin.sync import sync_sources
from test_sync import Client, workbook


class PublishedClient(Client):
    def __init__(self, repository, *, upstreams=None, native_repository=b""):
        super().__init__(published=True)
        self.repository = repository
        self.upstreams = upstreams or ["pub-luid"]
        self.native_repository = native_repository

    def download(self, group, identifier):
        if group == "workbooks":
            return workbook(identifier, published=True).replace(
                b'<repository-location id="pub-luid"/>', self.repository
            )
        return (
            b'<datasource name="published">'
            + self.native_repository
            + b'<connection class="mysql"><relation type="text">SELECT 1 AS x</relation>'
            b"</connection></datasource>"
        )

    def workbook_lineage(self, identifier):
        return {
            "workbook": {"id": "metadata:book", "luid": identifier},
            "tables": [],
            "flows": [],
            "datasources": [
                {"id": "metadata:" + luid, "luid": luid, "name": "Sales"}
                for luid in self.upstreams
            ],
        }


@pytest.mark.parametrize(
    "repository",
    [
        b'<repository-location id="published-sales"/>',
        b'<repository-location id="14296841" derived-from="published-sales"/>',
    ],
)
def test_rest_identity_survives_stale_native_derived_from(
    tmp_path, monkeypatch, repository
):
    monkeypatch.chdir(tmp_path)
    client = PublishedClient(
        repository,
        native_repository=b'<repository-location id="published-sales" derived-from="/datasources/old-source?rev=1.0"/>',
    )
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    published = next(a for a in bundle["assets"] if a["id"] == "dataset:pub-luid")
    assert published["source"]["content_url"] == "published-sales"
    assert published["source"]["luid"] == "pub-luid"
    assert published["source"]["metadata_id"] == "metadata:pub-luid"
    assert (
        published["definition"]["repository_locations"][0]["derived-from"]
        == "/datasources/old-source?rev=1.0"
    )
    assert {
        "from": "dataset:book1:source",
        "to": "dataset:pub-luid",
        "relation": "published_datasource",
    } in bundle["dependencies"]
    assert not bundle["coverage"]
    assert result["status"] == "ok"


@pytest.mark.parametrize(
    "repository",
    [
        b'<repository-location id="published-sales"/>',
        b'<repository-location id="pub-luid" derived-from="published-sales"/>',
    ],
)
def test_ambiguous_stable_references_do_not_bind(tmp_path, monkeypatch, repository):
    monkeypatch.chdir(tmp_path)
    client = PublishedClient(repository, upstreams=["pub-luid", "other-luid"])
    bundle = inspect_bundle(sync_sources(client, all_dashboards=True)["output_dir"])
    assert not [
        e for e in bundle["dependencies"] if e["relation"] == "published_datasource"
    ]
    assert any(c["code"] == "published_binding_unresolved" for c in bundle["coverage"])
    assert any(c["code"] == "lineage_binding_unresolved" for c in bundle["coverage"])


def test_display_name_is_not_used_as_a_published_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = PublishedClient(b'<repository-location id="Sales"/>')
    bundle = inspect_bundle(sync_sources(client, all_dashboards=True)["output_dir"])
    assert not [
        e for e in bundle["dependencies"] if e["relation"] == "published_datasource"
    ]
    assert any(c["code"] == "published_binding_unresolved" for c in bundle["coverage"])


def test_proxy_tables_use_the_published_dataset_boundary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = PublishedClient(
        b'<repository-location id="published-sales"/><connection class="sqlproxy" composed-connection-name="proxy">'
        b'<relation connection="proxy" type="table" table="[sqlproxy]"/></connection>'
        b'<object-graph><object><properties><relation connection="proxy" type="table" table="[sqlproxy]"/></properties></object></object-graph>'
    )
    result = sync_sources(client, all_dashboards=True)
    bundle = inspect_bundle(result["output_dir"])
    assert not [a for a in bundle["assets"] if a["kind"] == "external_source"]
    assert not bundle["coverage"]
    assert result["status"] == "ok"
    assert b"[sqlproxy]" in b"".join(
        (Path(result["output_dir"]) / name).read_bytes()
        for name in bundle["manifest"]["files"]
        if name.startswith("native/")
    )


def test_proxy_without_repository_identity_stays_unresolved():
    parsed = parse_datasource(
        b'<datasource><connection class="sqlproxy"><relation type="table" table="[sqlproxy]"/></connection></datasource>',
        datasource_id="published",
    )
    assert [c["code"] for c in parsed["coverage"]] == ["published_identity_missing"]


@pytest.mark.parametrize(
    "producing_relation,tables,queries,coverage",
    [
        (b'<relation type="text">SELECT 1 AS x</relation>', [], ["SELECT 1 AS x"], []),
        (
            b'<relation type="table" table="[public].[orders]"/>',
            ["[public].[orders]"],
            [],
            ["external_etl_unresolved"],
        ),
        (b"", [], [], ["extract_source_unresolved"]),
    ],
)
def test_extract_context_preserves_real_source_boundaries(
    producing_relation, tables, queries, coverage
):
    parsed = parse_datasource(
        b'<datasource><connection class="mysql">'
        + producing_relation
        + b'</connection><extract><connection class="hyper"><relation type="table" table="[Extract].[Extract]"/></connection></extract>'
        b'<object-graph><object><properties context="extract"><relation type="table" table="[Extract].[Extract]"/></properties></object></object-graph></datasource>',
        datasource_id="published",
    )
    external = [a for a in parsed["assets"] if a["kind"] == "external_source"]
    assert [a["definition"]["table"] for a in external] == tables
    assert [c["code"] for c in parsed["coverage"]] == coverage
    assert [q["text"] for q in parsed["queries"]] == queries
    assert "Extract" in next(iter(parsed["documents"].values()))


def test_direct_hyper_table_is_still_a_physical_source():
    parsed = parse_datasource(
        b'<datasource><connection class="hyper"><relation type="table" table="[Extract].[Extract]"/></connection></datasource>',
        datasource_id="published",
    )
    assert [c["code"] for c in parsed["coverage"]] == ["external_etl_unresolved"]
