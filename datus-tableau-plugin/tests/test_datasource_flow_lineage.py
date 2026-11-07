from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from datus_tableau_plugin.bundle import inspect_bundle
from datus_tableau_plugin.client import TableauClient
from datus_tableau_plugin.errors import MetadataError
from datus_tableau_plugin.sync import sync_sources

FIXTURE = Path(__file__).parent / "fixtures/published-prep-lineage"
DS = "3c19424e-f418-441e-b634-1ae535e25996"
FLOW = "1b7d9207-23ef-42ff-823f-20041d19a348"
BOOK = "0ca853bf-bc1f-481c-aad9-a78b2f91e9f8"
TABLE = "f4e38a26-e9e0-25bc-39b5-0b33ea7b8d2e"


class ReplayClient:
    def __init__(self):
        self.context = json.loads((FIXTURE / "context.json").read_text())
        self.settings = SimpleNamespace(**self.context["source"])
        self.lineages = {
            x["workbook"]["luid"]: x
            for x in [
                json.loads(p.read_text()) for p in (FIXTURE / "metadata").glob("*.json")
            ]
        }
        self.calls = Counter()
        self.scoped = {
            "datasource": copy.deepcopy(self.lineages[BOOK]["datasources"][0]),
            "flows": copy.deepcopy(self.lineages[BOOK]["flows"]),
            "tables": copy.deepcopy(self.lineages[BOOK]["tables"]),
        }
        self.mutate_native = lambda group, identifier, payload: payload

    def list_resources(self, group):
        return [
            {
                "id": k,
                "name": v["workbook"]["name"],
                "project_id": self.context["selection"]["project_ids"][0],
            }
            for k, v in self.lineages.items()
        ]

    def get_resource(self, group, identifier):
        return self.context["rest"].get(
            identifier, {"id": identifier, "connections": []}
        )

    def download(self, group, identifier):
        self.calls[(group, identifier)] += 1
        names = {
            ("workbooks", BOOK): "9bae0927ab6123a141ca3ad8.xml",
            (
                "workbooks",
                "598554f9-7e4f-44ec-aff9-25d92154b6e2",
            ): "c45d47fdab2542f158f184a5.xml",
            ("datasources", DS): "ea287d338b5ec4437a0f1016.xml",
            (
                "datasources",
                "28f38d5c-cc2e-4717-a203-effb2f2ec5d6",
            ): "56882102fb59515d679e0f27.xml",
            ("flows", FLOW): "7291cb570101a8fd1a113a6e.xml",
        }
        payload = (FIXTURE / "native" / names[(group, identifier)]).read_bytes()
        return self.mutate_native(group, identifier, payload)

    def workbook_lineage(self, identifier):
        return self.lineages[identifier]

    def datasource_lineage(self, identifier):
        self.calls[("datasource_lineage", identifier)] += 1
        assert identifier == DS
        return self.scoped

    def workbook_custom_sql(self, identifier):
        if identifier == BOOK:
            return []
        from xml.etree import ElementTree as ET

        sql = (
            ET.fromstring(
                (FIXTURE / "native/56882102fb59515d679e0f27.xml").read_bytes()
            )
            .find("./connection/relation")
            .text.strip()
        )
        return [
            {
                "id": "91670bd8-e5b5-406a-38dc-1b3d42a2f019",
                "name": "Custom SQL Query",
                "query": sql,
                "isUnsupportedCustomSql": False,
            }
        ]


def replay(client, tmp_path, monkeypatch, *, bound=True):
    monkeypatch.chdir(tmp_path)
    result = sync_sources(
        client,
        project_ids=client.context["selection"]["project_ids"],
        source_bindings=client.context["bindings"] if bound else None,
    )
    return result, inspect_bundle(result["output_dir"])


def test_actual_native_bound_flow_resolves_its_materialization(tmp_path, monkeypatch):
    client = ReplayClient()
    for path, expected in client.context["native_and_metadata_sha256"].items():
        assert hashlib.sha256((FIXTURE / path).read_bytes()).hexdigest() == expected
    result, bundle = replay(client, tmp_path, monkeypatch)
    assert result["status"] == "ok"
    assert not bundle["coverage"]
    assert client.calls[("datasource_lineage", DS)] == 1
    assert {
        "from": "dataset:" + DS,
        "to": "transform:" + FLOW,
        "relation": "upstream_flow",
    } in bundle["dependencies"]
    boundary = next(
        a for a in bundle["assets"] if a["id"] == "external:528121ee2b2b1704afcf"
    )
    assert boundary["definition"]["etl_definition"]["upstream_flow_luids"] == [FLOW]
    assert boundary["definition"]["etl_definition"]["metadata_table_id"] == TABLE
    validation = json.loads(
        (Path(result["output_dir"]) / "validation.json").read_text()
    )
    assert validation["generated"] == 7
    assert validation["blocked"] == 0


def test_unbound_flow_keeps_processing_blockers(tmp_path, monkeypatch):
    client = ReplayClient()
    result, bundle = replay(client, tmp_path, monkeypatch, bound=False)
    assert result["status"] == "partial"
    assert (
        len([c for c in bundle["coverage"] if c["code"] == "processing_model_blocked"])
        == 7
    )
    assert not [
        c for c in bundle["coverage"] if c["code"] == "lineage_binding_unresolved"
    ]


@pytest.mark.parametrize(
    "mode",
    [
        "no_marker",
        "append_output",
        "wrong_table",
        "wrong_connection",
        "ambiguous_table",
        "wrong_luid",
        "wrong_metadata_id",
        "hidden_flow",
        "multiple_flows",
        "conflicting_flow_identity",
        "failed_flow",
        "metadata_denied",
    ],
)
def test_materialization_resolution_fails_closed(tmp_path, monkeypatch, mode):
    client = ReplayClient()
    if mode == "no_marker":
        client.mutate_native = lambda group, identifier, payload: payload.replace(
            b'prep-output-type="outputOperationTypeCreate"', b""
        )
    elif mode == "append_output":
        client.mutate_native = lambda group, identifier, payload: payload.replace(
            b"outputOperationTypeCreate", b"outputOperationTypeAppend"
        )
    elif mode == "wrong_table":
        client.scoped["tables"][0]["fullName"] = "[Elsewhere].[Extract]"
    elif mode == "wrong_connection":
        client.scoped["tables"][0]["connectionType"] = "postgres"
    elif mode == "ambiguous_table":
        client.scoped["tables"].append(
            {**client.scoped["tables"][0], "id": "different-table-id"}
        )
    elif mode == "wrong_luid":
        client.scoped["datasource"]["luid"] = "unrelated"
    elif mode == "wrong_metadata_id":
        client.scoped["datasource"]["id"] = "unrelated"
    elif mode == "hidden_flow":
        client.scoped["flows"][0].pop("luid")
    elif mode == "multiple_flows":
        client.scoped["flows"].append(
            {"id": "m-other", "luid": "other", "name": "same"}
        )
    elif mode == "conflicting_flow_identity":
        client.scoped["flows"].append(
            {**client.scoped["flows"][0], "id": "different-metadata-id"}
        )
    elif mode == "failed_flow":
        client.mutate_native = lambda group, identifier, payload: (
            b"invalid" if group == "flows" else payload
        )
    elif mode == "metadata_denied":

        def denied(identifier):
            raise MetadataError("denied")

        client.datasource_lineage = denied
    result, bundle = replay(client, tmp_path, monkeypatch, bound=mode != "failed_flow")
    assert result["status"] == "partial"
    assert any(c["code"] == "external_etl_unresolved" for c in bundle["coverage"])
    assert any(c["code"] == "external_definition_required" for c in bundle["coverage"])


def metadata_page(connection, *, luid="ds", metadata_id="m-ds", after=None):
    return {
        "publishedDatasources": [
            {
                "id": metadata_id,
                "luid": luid,
                "name": "irrelevant",
                connection: {
                    "nodes": [{"id": connection + str(after)}],
                    "pageInfo": {"hasNextPage": after is None, "endCursor": "p2"},
                },
            }
        ]
    }


def test_datasource_connections_page_independently():
    client = object.__new__(TableauClient)
    calls = []

    def metadata(query, variables):
        connection = next(
            x
            for x in ("upstreamFlowsConnection", "upstreamTablesConnection")
            if x in query
        )
        assert "publishedDatasources(filter: {luid: $luid})" in query
        calls.append((connection, variables.copy()))
        return metadata_page(connection, after=variables["after"])

    client.metadata = metadata
    result = client.datasource_lineage("ds")
    assert result["datasource"]["id"] == "m-ds"
    assert len(result["flows"]) == len(result["tables"]) == 2
    assert [(c, v["after"]) for c, v in calls] == [
        (c, p)
        for c in ("upstreamFlowsConnection", "upstreamTablesConnection")
        for p in (None, "p2")
    ]


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_luid",
        "missing_id",
        "changing_identity",
        "missing_connection",
        "stuck_cursor",
    ],
)
def test_datasource_metadata_rejects_incomplete_identity_or_pagination(mode):
    client = object.__new__(TableauClient)

    def metadata(query, variables):
        connection = next(
            x
            for x in ("upstreamFlowsConnection", "upstreamTablesConnection")
            if x in query
        )
        page = metadata_page(connection, after=variables["after"])
        row = page["publishedDatasources"][0]
        if mode == "wrong_luid":
            row["luid"] = "other"
        elif mode == "missing_id":
            row.pop("id")
        elif mode == "changing_identity" and variables["after"]:
            row["id"] = "other"
        elif mode == "missing_connection":
            row.pop(connection)
        elif mode == "stuck_cursor":
            row[connection]["pageInfo"]["hasNextPage"] = True
        return page

    client.metadata = metadata
    with pytest.raises(MetadataError):
        client.datasource_lineage("ds")


def test_unresolved_published_edges_do_not_prove_flow_binding(tmp_path, monkeypatch):
    client = ReplayClient()

    def break_reference(group, identifier, payload):
        if group == "workbooks" and identifier == BOOK:
            return payload.replace(
                b'id="Datus_E2E_Disposable_Prep_Output_20261107"', b'id="unknown"'
            ).replace(
                b'derived-from="http://localhost:9100/t/louisliu-6562d45ba2/datasources/Datus_E2E_Disposable_Prep_Output_20261107"',
                b'derived-from="unknown"',
            )
        return payload

    client.mutate_native = break_reference
    result, bundle = replay(client, tmp_path, monkeypatch)
    assert result["status"] == "partial"
    assert any(
        c["asset_id"] == "chart:" + BOOK + ":Sheet 1"
        and c["code"] == "lineage_binding_unresolved"
        for c in bundle["coverage"]
    )
    assert any(c["code"] == "published_binding_unresolved" for c in bundle["coverage"])


def test_shared_hyper_table_keeps_unrelated_consumer_gap(tmp_path, monkeypatch):
    client = ReplayClient()
    client.lineages["598554f9-7e4f-44ec-aff9-25d92154b6e2"]["tables"] = copy.deepcopy(
        client.scoped["tables"]
    )
    result, bundle = replay(client, tmp_path, monkeypatch)
    assert result["status"] == "partial"
    assert any(
        c["asset_id"] == "external_source:table:" + TABLE
        and c["code"] == "external_definition_required"
        for c in bundle["coverage"]
    )
    assert not [
        e
        for e in bundle["dependencies"]
        if e["from"] == "external_source:table:" + TABLE
        and e["relation"] == "materialized_from"
    ]


def test_flow_gap_clears_only_for_chart_with_bound_dataset_path(tmp_path, monkeypatch):
    client = ReplayClient()

    def add_unrelated_chart(group, identifier, payload):
        if group != "workbooks" or identifier != BOOK:
            return payload
        payload = payload.replace(
            b"</datasources>",
            b'<datasource name="unrelated"><connection class="mysql"><relation type="text">SELECT 2 AS x</relation></connection></datasource></datasources>',
            1,
        )
        payload = payload.replace(
            b"</worksheets>",
            b'<worksheet name="Unrelated"><table><view><datasources><datasource name="unrelated"/></datasources></view></table></worksheet></worksheets>',
            1,
        )
        return payload.replace(
            b"</dashboards>",
            b'<dashboard name="Unrelated"><zones><zone name="Unrelated"/></zones></dashboard></dashboards>',
            1,
        )

    client.mutate_native = add_unrelated_chart
    result, bundle = replay(client, tmp_path, monkeypatch)
    assert result["status"] == "partial"
    issues = {
        c["asset_id"]
        for c in bundle["coverage"]
        if c["code"] == "lineage_binding_unresolved"
    }
    assert "chart:" + BOOK + ":Unrelated" in issues
    assert "chart:" + BOOK + ":Sheet 1" not in issues


@pytest.mark.parametrize(
    "mode", ["empty_flows", "empty_tables", "unknown_scope", "update_output"]
)
def test_incomplete_scope_or_update_does_not_resolve_materialization(
    tmp_path, monkeypatch, mode
):
    client = ReplayClient()
    if mode == "empty_flows":
        client.scoped["flows"] = []
    elif mode == "empty_tables":
        client.scoped["tables"] = []
    elif mode == "unknown_scope":
        client.scoped = None
    elif mode == "update_output":
        client.mutate_native = lambda group, identifier, payload: payload.replace(
            b"outputOperationTypeCreate", b"outputOperationTypeUpdate"
        )
    result, bundle = replay(client, tmp_path, monkeypatch)
    assert result["status"] == "partial"
    assert any(c["code"] == "external_etl_unresolved" for c in bundle["coverage"])
    assert any(c["code"] == "external_definition_required" for c in bundle["coverage"])


@pytest.mark.parametrize(
    "mode", ["empty_root", "hidden_page_info", "page_limit", "row_limit"]
)
def test_datasource_metadata_permission_completeness_and_limits(mode):
    client = object.__new__(TableauClient)
    calls = []

    def metadata(query, variables):
        calls.append(variables.copy())
        connection = next(
            x
            for x in ("upstreamFlowsConnection", "upstreamTablesConnection")
            if x in query
        )
        page = metadata_page(connection, after=variables["after"])
        if mode == "empty_root":
            page["publishedDatasources"] = []
            return page
        body = page["publishedDatasources"][0][connection]
        if mode == "hidden_page_info":
            body.pop("pageInfo")
        elif mode == "page_limit":
            body["nodes"] = []
            body["pageInfo"] = {"hasNextPage": True, "endCursor": str(len(calls))}
        elif mode == "row_limit":
            body["nodes"] = [{"id": "one"}] * 100001
            body["pageInfo"] = {"hasNextPage": False}
        return page

    client.metadata = metadata
    with pytest.raises(MetadataError):
        client.datasource_lineage("ds")
    assert len(calls) <= 1000


@pytest.mark.parametrize("unrelated_output", [False, True])
def test_empty_native_flow_does_not_resolve_materialization(
    tmp_path, monkeypatch, unrelated_output
):
    client = ReplayClient()
    original_download = client.download
    if unrelated_output:
        client.lineages[BOOK]["flows"].append(
            {
                "id": "metadata:unrelated-flow",
                "luid": "unrelated-flow",
                "name": "Same display name",
            }
        )

    def download(group, identifier):
        if group == "flows" and identifier == FLOW:
            return b'{"nodes":{}}'
        if group == "flows" and identifier == "unrelated-flow":
            return original_download(group, FLOW)
        return original_download(group, identifier)

    client.download = download
    result, bundle = replay(client, tmp_path, monkeypatch, bound=False)
    assert result["status"] == "partial"
    assert {
        "from": "dataset:" + DS,
        "to": "transform:" + FLOW,
        "relation": "upstream_flow",
    } in bundle["dependencies"]
    assert any(
        c["asset_id"] == "external:528121ee2b2b1704afcf"
        and c["code"] == "external_etl_unresolved"
        for c in bundle["coverage"]
    )
    assert any(
        c["asset_id"] == "external_source:table:" + TABLE
        and c["code"] == "external_definition_required"
        for c in bundle["coverage"]
    )
