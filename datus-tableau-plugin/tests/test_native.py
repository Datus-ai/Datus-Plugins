from __future__ import annotations

import io
import json
import zipfile
import stat
from pathlib import Path

import pytest

from datus_tableau_plugin.native import parse_datasource, parse_flow, parse_workbook

FIXTURES = Path(__file__).parent / "fixtures"


def zipped(files: dict[str, bytes]) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return data.getvalue()


def test_workbook_retains_layout_dependencies_custom_sql_and_complex_field_scope():
    source = (FIXTURES / "analysis.twb").read_bytes()
    result = parse_workbook(source, workbook_id="wb-one")
    assets = {item["id"]: item for item in result["assets"]}
    assert {
        item["name"] for item in assets.values() if item["kind"] == "dashboard"
    } == {"Regional overview", "Fixed overview"}
    assert assets["dashboard:wb-one:Regional overview"]["definition"]["worksheets"] == [
        "chart:wb-one:Sales by Region"
    ]
    assert assets["chart:wb-one:Sales by Region"]["status"] == "ok"
    assert assets["chart:wb-one:Fixed Sales"]["status"] == "unsupported"
    assert assets["dataset:wb-one:sales"]["status"] == "ok"
    assert any(item.get("field_id") == "[FixedSales]" for item in result["coverage"])
    assert result["queries"][0]["text"] == "SELECT id, region FROM public.customers"
    assert result["queries"][0]["origin"] == "source"
    assert {
        "from": "chart:wb-one:Sales by Region",
        "to": "dataset:wb-one:sales",
        "relation": "depends_on",
    } in result["dependencies"]
    assert (
        assets["dataset:wb-one:published-sales"]["source"]["published_id"]
        == "sales-content-name"
    )
    assert "luid" not in assets["dataset:wb-one:published-sales"]["source"]
    serialized = json.dumps(result)
    assert "synthetic-secret" not in serialized and "synthetic-user" not in serialized
    assert "warehouse.example.test" in serialized and "analytics" in serialized


def test_ids_do_not_collide_between_workbooks_with_same_display_names():
    payload = (FIXTURES / "analysis.twb").read_bytes()
    a = parse_workbook(payload, workbook_id="a")
    b = parse_workbook(payload, workbook_id="b")
    assert not {x["id"] for x in a["assets"]} & {x["id"] for x in b["assets"]}


def test_packaged_workbook_only_reads_definition_and_never_returns_data():
    result = parse_workbook(
        zipped(
            {
                "book.twb": (FIXTURES / "analysis.twb").read_bytes(),
                "Data/secret.hyper": b"private extracted rows",
            }
        ),
        workbook_id="wb",
    )
    assert len(result["documents"]) == 1
    assert "private extracted rows" not in json.dumps(result)


@pytest.mark.parametrize(
    "name", ["../outside.twb", "/absolute.twb", "Data\\outside.twb"]
)
def test_archive_rejects_traversal_even_in_unused_members(name):
    with pytest.raises(ValueError, match="unsafe"):
        parse_workbook(
            zipped({"book.twb": b"<workbook/>", name: b"<workbook/>"}), workbook_id="wb"
        )


def test_duplicate_archive_definitions_are_not_guessed():
    with pytest.raises(ValueError, match="exactly one"):
        parse_workbook(
            zipped({"a.twb": b"<workbook/>", "b.twb": b"<workbook/>"}), workbook_id="wb"
        )


@pytest.mark.parametrize(
    "payload",
    [
        b'<!DOCTYPE workbook [<!ENTITY secret "LEAK">]><workbook>&secret;</workbook>',
        '<!DOCTYPE workbook [<!ENTITY secret "LEAK">]><workbook>&secret;</workbook>'.encode(
            "utf-16"
        ),
    ],
)
def test_xml_entities_and_dtd_are_rejected_in_any_encoding(payload):
    with pytest.raises(ValueError, match="unsafe"):
        parse_workbook(payload, workbook_id="wb")


def test_standalone_datasource_does_not_claim_upstream_etl_is_present():
    result = parse_datasource(
        b'<datasource name="sales"><connection class="postgres" dbname="a"><relation type="table" table="[public].[sales]"/></connection></datasource>',
        datasource_id="ds-luid",
    )
    assert result["assets"][0]["source"]["luid"] == "ds-luid"
    assert any(item["code"] == "external_etl_unresolved" for item in result["coverage"])


def test_prep_zip_flow_entry_preserves_topology_nested_operations_and_parameters():
    result = parse_flow(
        zipped(
            {
                "flow": (FIXTURES / "prep-flow.json").read_bytes(),
                "Data/input.csv": b"private rows",
            }
        ),
        flow_id="flow-luid",
    )
    assets = {item["id"]: item for item in result["assets"]}
    assert assets["transform:flow-luid:clean"]["definition"]["inputs"] == [
        "transform:flow-luid:input"
    ]
    assert assets["transform:flow-luid:aggregate"]["definition"]["inputs"] == [
        "transform:flow-luid:clean"
    ]
    assert (
        assets["transform:flow-luid:clean/trim"]["definition"]["operation"]["native"][
            "expression"
        ]
        == "TRIM([Region])"
    )
    assert assets["transform:flow-luid:script"]["status"] == "unsupported"
    assert (
        assets["transform:flow-luid"]["definition"]["parameters"]["as_of"]["value"]
        == "2026-11-03"
    )
    assert "private rows" not in json.dumps(result)
    assert "synthetic-secret" not in json.dumps(result)


def test_unknown_flow_shape_fails_instead_of_successful_empty_result():
    with pytest.raises(ValueError, match="nodes"):
        parse_flow(b'{"name":"new format"}', flow_id="flow")


def test_archive_rejects_symlink_and_compression_bomb():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        info = zipfile.ZipInfo("book.twb")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "elsewhere.twb")
    with pytest.raises(ValueError, match="symbolic link"):
        parse_workbook(data.getvalue(), workbook_id="wb")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("book.twb", b" " * 1000000)
    with pytest.raises(ValueError, match="decompression"):
        parse_workbook(data.getvalue(), workbook_id="wb")


def test_self_join_reuses_one_external_table_asset():
    payload = b'<datasource name="s"><connection><relation type="join" join="left"><relation type="table" table="orders"/><relation type="table" table="orders"/></relation></connection></datasource>'
    result = parse_datasource(payload, datasource_id="s")
    identities = [item["id"] for item in result["assets"]]
    assert len(identities) == len(set(identities))


def test_flow_custom_sql_is_source_evidence_and_explicit_source_operation():
    result = parse_flow(
        b'{"nodes":{"source":{"nodeType":".v1.LoadSql","customSql":"SELECT id FROM sales"}}}',
        flow_id="f",
    )
    source = next(
        item for item in result["assets"] if item["id"] == "transform:f:source"
    )
    assert source["definition"]["operation"]["sql"] == "SELECT id FROM sales"
    assert result["queries"][0]["origin"] == "source"


def test_explicit_sql_mapping_survives_flow_reader_and_generates_models():
    import sqlite3

    from datus_tableau_plugin.transforms import generate_transforms

    document = {
        "nodes": {
            "source": {
                "nodeType": ".v1.LoadSql",
                "table": "sales",
                "nextNodes": [{"nextNodeId": "filter"}],
            },
            "filter": {
                "nodeType": ".v1.FilterOperation",
                "condition": "amount > 0",
                "expression_language": "sql",
                "source_dialect": "sqlite",
                "nextNodes": [{"nextNodeId": "pivot"}],
            },
            "pivot": {
                "nodeType": ".v1.Pivot",
                "direction": "columns_to_rows",
                "index": ["region"],
                "values": ["amount"],
                "names_to": "kind",
                "values_to": "value",
                "include_nulls": True,
            },
        }
    }
    parsed = parse_flow(json.dumps(document).encode(), flow_id="f")
    generated = generate_transforms(
        parsed["assets"], parsed["dependencies"], dialect="sqlite"
    )
    assert generated["validation"]["generated"] == 3
    assert generated["validation"]["blocked"] == 0
    pivot = next(
        item
        for item in generated["pipeline"]["nodes"]
        if item["id"] == "transform:f:pivot"
    )
    with sqlite3.connect(":memory:") as database:
        database.executescript(
            "CREATE TABLE sales(region TEXT,amount INTEGER); INSERT INTO sales VALUES('East',10),('West',-5);"
        )
        assert database.execute(generated["models"][pivot["file"]]).fetchall() == [
            ("East", "amount", 10)
        ]


def test_native_clean_without_explicit_semantics_is_not_a_passthrough():
    parsed = parse_flow(
        b'{"nodes":{"clean":{"nodeType":".v1.Clean","groupValues":["A","B"]}}}',
        flow_id="f",
    )
    assert parsed["assets"][1]["status"] == "unsupported"


def test_flow_left_join_keeps_declared_input_order_despite_reversed_node_order():
    import sqlite3

    from datus_tableau_plugin.transforms import generate_transforms

    document = {
        "nodes": {
            "right": {
                "nodeType": ".v1.LoadSql",
                "table": "right_table",
                "nextNodes": [{"nextNodeId": "join"}],
            },
            "left": {
                "nodeType": ".v1.LoadSql",
                "table": "left_table",
                "nextNodes": [{"nextNodeId": "join"}],
            },
            "join": {
                "nodeType": ".v1.SimpleJoin",
                "inputs": ["left", "right"],
                "join_type": "LEFT",
                "condition": "l.id = r.id",
                "expression_language": "sql",
                "projection": [
                    {"side": "left", "column": "id"},
                    {"side": "right", "column": "value"},
                ],
            },
        }
    }
    parsed = parse_flow(json.dumps(document).encode(), flow_id="f")
    join = next(item for item in parsed["assets"] if item["id"] == "transform:f:join")
    assert join["definition"]["inputs"] == ["transform:f:left", "transform:f:right"]
    generated = generate_transforms(
        parsed["assets"], list(reversed(parsed["dependencies"])), dialect="sqlite"
    )
    result = next(
        item for item in generated["pipeline"]["nodes"] if item["id"] == join["id"]
    )
    assert result["status"] == "generated"
    with sqlite3.connect(":memory:") as database:
        database.executescript(
            "CREATE TABLE left_table(id INTEGER); CREATE TABLE right_table(id INTEGER,value TEXT); INSERT INTO left_table VALUES (1),(2); INSERT INTO right_table VALUES (2,'matched'),(3,'right-only');"
        )
        assert sorted(
            database.execute(generated["models"][result["file"]]).fetchall()
        ) == [(1, None), (2, "matched")]


@pytest.mark.parametrize("declared_inputs", [None, ["left"], ["left", "left"]])
def test_flow_join_next_nodes_topology_does_not_prove_left_and_right_inputs(
    declared_inputs,
):
    from datus_tableau_plugin.transforms import generate_transforms

    document = {
        "nodes": {
            "right": {
                "nodeType": ".v1.LoadSql",
                "table": "right_table",
                "nextNodes": [{"nextNodeId": "join"}],
            },
            "left": {
                "nodeType": ".v1.LoadSql",
                "table": "left_table",
                "nextNodes": [{"nextNodeId": "join"}],
            },
            "join": {
                "nodeType": ".v1.SimpleJoin",
                "join_type": "LEFT",
                "condition": "l.id = r.id",
                "expression_language": "sql",
                "projection": [{"side": "left", "column": "id"}],
            },
        }
    }
    if declared_inputs is not None:
        document["nodes"]["join"]["inputs"] = declared_inputs
    parsed = parse_flow(json.dumps(document).encode(), flow_id="f")
    join = next(item for item in parsed["assets"] if item["id"] == "transform:f:join")
    assert join["status"] == "unsupported"
    assert any(
        item["asset_id"] == join["id"] and item["code"] == "join_input_order_unresolved"
        for item in parsed["coverage"]
    )
    generated = generate_transforms(
        parsed["assets"], parsed["dependencies"], dialect="sqlite"
    )
    result = next(
        item for item in generated["pipeline"]["nodes"] if item["id"] == join["id"]
    )
    assert result["status"] == "blocked"
    assert "file" not in result


@pytest.mark.parametrize("field", ["odbc-connect-string-extras", "connection-string"])
@pytest.mark.parametrize("parser_kind", ["workbook", "datasource"])
def test_connection_strings_are_redacted_in_xml_attributes_and_element_text(
    field, parser_kind
):
    connection = f"""<connection class="postgres" server="warehouse.example.test" dbname="analytics"
        {field}="UID=reader;PWD=synthetic-attribute-password">
      <{field}>UID=reader;PWD=synthetic-element-password</{field}>
      <relation type="text">SELECT 'connection-string' AS label FROM public.sales</relation>
    </connection>"""
    datasource = f'<datasource name="sales">{connection}</datasource>'
    if parser_kind == "workbook":
        payload = f"<workbook><datasources>{datasource}</datasources></workbook>"
        parsed = parse_workbook(payload.encode(), workbook_id="book")
    else:
        parsed = parse_datasource(datasource.encode(), datasource_id="source")
    serialized = json.dumps(parsed)
    assert "synthetic-attribute-password" not in serialized
    assert "synthetic-element-password" not in serialized
    assert "warehouse.example.test" in serialized and "analytics" in serialized
    assert (
        parsed["queries"][0]["text"]
        == "SELECT 'connection-string' AS label FROM public.sales"
    )
    assert "[REDACTED]" in next(iter(parsed["documents"].values()))


@pytest.mark.parametrize(
    "field", ["odbc-connect-string-extras", "connection-string", "connectionString"]
)
def test_connection_strings_are_redacted_in_nested_prep_json(field):
    payload = {
        "flow": {
            "connections": {
                "warehouse": {
                    "server": "warehouse.example.test",
                    "database": "analytics",
                    "properties": {field: "UID=reader;PWD=synthetic-prep-password"},
                },
            },
            "nodes": {
                "input": {
                    "nodeType": "Input",
                    "table": "sales",
                    "connection": {field: "UID=reader;PWD=synthetic-node-password"},
                }
            },
        }
    }
    parsed = parse_flow(json.dumps(payload).encode(), flow_id="flow")
    serialized = json.dumps(parsed)
    assert "synthetic-prep-password" not in serialized
    assert "synthetic-node-password" not in serialized
    assert "warehouse.example.test" in serialized and "analytics" in serialized
    assert "[REDACTED]" in next(iter(parsed["documents"].values()))
