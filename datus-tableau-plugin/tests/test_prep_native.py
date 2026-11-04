"""Anonymized native shapes, executed against independent synthetic DuckDB rows."""

from __future__ import annotations

import hashlib
import json

import duckdb
import pytest

from datus_tableau_plugin.bindings import apply_source_bindings, read_bindings
from datus_tableau_plugin.errors import UsageError
from datus_tableau_plugin.formula import translate
from datus_tableau_plugin.native import parse_flow
from datus_tableau_plugin.plan import plan_flow
from datus_tableau_plugin.transforms import generate_transforms


def node(identifier, kind, **kwargs):
    return {
        "id": identifier,
        "nodeType": (".v2018_2_3." if kind.startswith("Super") else ".v1.") + kind,
        "baseType": "input" if kind.startswith("Load") else "transform",
        "nextNodes": [],
        **kwargs,
    }


def edge(target, namespace="Default", producer="Default"):
    return {"namespace": producer, "nextNodeId": target, "nextNamespace": namespace}


def source(identifier, fields, **kwargs):
    return node(
        identifier,
        "LoadCsv",
        fields=[{"name": k, "type": v} for k, v in fields.items()],
        **kwargs,
    )


def parsed(nodes):
    return parse_flow(json.dumps({"nodes": nodes}).encode(), flow_id="fixture")


def generate(document, mappings=None):
    if mappings is None:
        mappings = {
            a["id"]: {"table": a["source"]["native_id"].split("/")[0]}
            for a in document["assets"]
            if a["definition"]["operation"]["type"] == "source"
        }
    assets = apply_source_bindings(document["assets"], mappings)
    return generate_transforms(assets, document["dependencies"])


def model(result, identifier):
    row = next(
        r
        for r in result["pipeline"]["nodes"]
        if r["id"] == "transform:fixture:" + identifier
    )
    assert row["status"] == "generated", row
    return result["models"][row["file"]]


def test_container_ports_actions_and_before_after_annotations_execute_in_order():
    # Dictionary order is deliberately opposite to the execution edges.
    child = node(
        "replace",
        "AddColumn",
        columnName="value",
        expression="[value] + 1",
        nextNodes=[edge("rename")],
    )
    container = node(
        "box",
        "Container",
        baseType="container",
        loomContainer={
            "nodes": {
                "rename": node(
                    "rename", "RenameColumn", columnName="value", rename="amount"
                ),
                "replace": child,
            }
        },
        namespacesToInput={"Default": {"nodeId": "replace", "namespace": "Default"}},
        namespacesToOutput={"Default": {"nodeId": "rename", "namespace": "Default"}},
        nextNodes=[edge("sum")],
    )
    aggregate = node(
        "sum",
        "SuperAggregate",
        baseType="superNode",
        beforeActionAnnotations=[
            {
                "namespace": "Default",
                "annotationNode": node(
                    "before",
                    "AddColumn",
                    columnName="amount",
                    expression="[amount] * 2",
                ),
            }
        ],
        actionNode=node(
            "action",
            "Aggregate",
            groupByFields=[],
            aggregateFields=[
                {"columnName": "amount", "function": "SUM", "newColumnName": "total"}
            ],
        ),
        afterActionAnnotations=[
            {
                "namespace": "Default",
                "annotationNode": node(
                    "after", "AddColumn", columnName="total", expression="[total] + 10"
                ),
            }
        ],
    )
    src = source(
        "input",
        {"value": "integer", "drop": "string"},
        actions=[node("remove", "RemoveColumns", columnNames=["drop"])],
        selectedFields=["value"],
        nextNodes=[edge("box")],
    )
    doc = parsed({"sum": aggregate, "box": container, "input": src})
    result = generate(doc)
    assert result["validation"]["blocked"] == 0
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(value INTEGER, drop VARCHAR)")
    connection.execute("INSERT INTO input VALUES (2,'a'), (4,'b')")
    assert connection.execute(model(result, "sum/after/0:after")).fetchall() == [(26,)]
    # Original native source remains unbound and wrapper controls are not models.
    assert not next(a for a in doc["assets"] if a["id"].endswith(":input"))[
        "definition"
    ]["operation"].get("table")
    assert all(
        n["operation"] not in {"flow", "container"} for n in result["pipeline"]["nodes"]
    )
    assert len(result["pipeline"]["nodes"]) == 8


@pytest.mark.parametrize(
    "join_type,expected",
    [
        (
            "left",
            [
                (1, "a", 1, "x"),
                (1, "a", 1, "y"),
                (2, "b", None, None),
                (None, "n", None, None),
            ],
        ),
        (
            "right",
            [
                (1, "a", 1, "x"),
                (1, "a", 1, "y"),
                (None, None, 3, "z"),
                (None, None, None, "r"),
            ],
        ),
    ],
)
def test_join_resolves_explicit_ports_many_matches_nulls_and_colliding_fields(
    join_type, expected
):
    left = source(
        "l", {"id": "integer", "value": "string"}, nextNodes=[edge("join", "Left")]
    )
    right = source(
        "r", {"id": "integer", "value": "string"}, nextNodes=[edge("join", "Right")]
    )
    join = node(
        "join",
        "SimpleJoin",
        joinType=join_type,
        conditions=[
            {"leftExpression": "[id]", "rightExpression": "[id]", "comparator": "=="}
        ],
    )
    doc = parsed({"r": right, "join": join, "l": left})
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE l(id INTEGER, value VARCHAR)")
    connection.execute("CREATE TABLE r(id INTEGER, value VARCHAR)")
    connection.execute("INSERT INTO l VALUES (1,'a'),(2,'b'),(NULL,'n')")
    connection.execute("INSERT INTO r VALUES (1,'x'),(1,'y'),(3,'z'),(NULL,'r')")
    cursor = connection.execute(model(result, "join"))
    assert [d[0] for d in cursor.description] == ["id", "value", "id-1", "value-1"]
    assert sorted(cursor.fetchall(), key=repr) == sorted(expected, key=repr)
    assert next(a for a in doc["assets"] if a["id"].endswith(":join"))["definition"][
        "inputs"
    ] == ["transform:fixture:l", "transform:fixture:r"]


def test_ambiguous_join_ports_fail_without_using_dictionary_order():
    doc = parsed(
        {
            "a": source("a", {"id": "integer"}, nextNodes=[edge("join")]),
            "b": source("b", {"id": "integer"}, nextNodes=[edge("join")]),
            "join": node("join", "SimpleJoin", joinType="left", conditions=[]),
        }
    )
    result = generate(doc)
    row = next(n for n in result["pipeline"]["nodes"] if n["id"].endswith(":join"))
    assert row["status"] == "blocked"
    assert "same namespace" in row["error"]
    assert result["validation"]["generated"] == 2


def test_input_union_aligns_names_gaps_and_preserves_literal_file_path_provenance():
    union = node(
        "files",
        "LoadCsvInputUnion",
        fields=[],
        pathNameField="File Paths",
        generatedInputs=[
            {
                "inputNode": source("first", {"a": "integer", "b": "string"}),
                "filePath": "first.csv",
            },
            {
                "inputNode": source("second", {"b": "string", "c": "integer"}),
                "filePath": "second.csv",
            },
        ],
    )
    doc = parsed({"files": union})
    result = generate(
        doc,
        {
            "transform:fixture:files/generated/first": {"table": "first_input"},
            "transform:fixture:files/generated/second": {"table": "second_input"},
        },
    )
    connection = duckdb.connect()
    connection.execute("CREATE TABLE first_input(a INTEGER,b VARCHAR)")
    connection.execute("CREATE TABLE second_input(b VARCHAR,c INTEGER)")
    connection.execute("INSERT INTO first_input VALUES (7,'left')")
    connection.execute("INSERT INTO second_input VALUES ('right',9)")
    cursor = connection.execute(model(result, "files"))
    assert [c[0] for c in cursor.description] == ["a", "b", "File Paths", "c"]
    assert cursor.fetchall() == [
        (7, "left", "first.csv", None),
        (None, "right", "second.csv", 9),
    ]


def test_simple_union_keeps_provenance_and_type_uncertainty_visible():
    doc = parsed(
        {
            "a": source("a", {"x": "string"}, nextNodes=[edge("u", "A")]),
            "b": source("b", {"x": "real"}, nextNodes=[edge("u", "B")]),
            "u": node(
                "u",
                "SimpleUnion",
                namespaceFieldMappings=[
                    {"namespaceName": "A", "fieldMappings": {}},
                    {"namespaceName": "B", "fieldMappings": {}},
                ],
            ),
        }
    )
    result = generate(doc)
    row = next(n for n in result["pipeline"]["nodes"] if n["id"].endswith(":u"))
    assert row["status"] == "blocked"
    assert row["local_semantic_issues"] == [
        {"code": "native_union_type_conflict", "fields": ["x"]},
        {"code": "native_union_provenance_unresolved", "field": "Table Names"},
    ]


def unpivot(values=("a", "b")):
    return node(
        "pivot",
        "Unpivot",
        unpivotGroups=[
            {
                "expressions": [
                    {
                        "bindings": [
                            {
                                "bindingType": "literal",
                                "newColumnName": "period",
                                "groupName": label,
                            },
                            {
                                "bindingType": "column",
                                "newColumnName": "amount",
                                "columnName": name,
                            },
                        ]
                    }
                    for name, label in zip(values, ["first", "second"], strict=True)
                ]
            }
        ],
    )


def test_unpivot_ordered_aliases_literal_names_and_null_rows_then_aggregate():
    pivot = unpivot()
    pivot["nextNodes"] = [edge("aggregate")]
    doc = parsed(
        {
            "input": source(
                "input",
                {"key": "string", "a": "integer", "b": "integer"},
                nextNodes=[edge("pivot")],
            ),
            "pivot": pivot,
            "aggregate": node(
                "aggregate",
                "Aggregate",
                groupByFields=[{"columnName": "period", "newColumnName": "label"}],
                aggregateFields=[
                    {
                        "columnName": "amount",
                        "function": "SUM",
                        "newColumnName": "total",
                    },
                    {"columnName": "key", "function": "COUNT", "newColumnName": "rows"},
                ],
            ),
        }
    )
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(key VARCHAR,a INTEGER,b INTEGER)")
    connection.execute("INSERT INTO input VALUES ('one',2,NULL),('two',3,8)")
    assert connection.execute(model(result, "pivot")).fetchall() == [
        ("one", "first", 2),
        ("two", "first", 3),
        ("one", "second", None),
        ("two", "second", 8),
    ]
    assert sorted(connection.execute(model(result, "aggregate")).fetchall()) == [
        ("first", 5, 2),
        ("second", 8, 2),
    ]


def test_unpivot_missing_native_field_is_blocked_without_inventing_it():
    doc = parsed(
        {
            "input": source(
                "input", {"key": "string", "a": "integer"}, nextNodes=[edge("pivot")]
            ),
            "pivot": unpivot(),
        }
    )
    result = generate(doc)
    row = next(n for n in result["pipeline"]["nodes"] if n["id"].endswith(":pivot"))
    assert row["error"] == "native_pivot_field_missing: b"
    assert result["validation"]["generated"] == 1


def test_remap_nulls_and_exclusion_range_three_valued_semantics():
    doc = parsed(
        {
            "input": source(
                "input", {"name": "string", "value": "real"}, nextNodes=[edge("remap")]
            ),
            "remap": node(
                "remap",
                "Remap",
                columnName="name",
                values={'"A"': ['"a"', '"a."'], '"missing"': ["null"]},
                nextNodes=[edge("range")],
            ),
            "range": node(
                "range",
                "RangeFilter",
                ranges={
                    "value": [
                        {
                            "startValue": "17",
                            "endValue": "18",
                            "includeStart": True,
                            "includeEnd": False,
                        }
                    ]
                },
                exclude=True,
            ),
        }
    )
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(name VARCHAR,value DOUBLE)")
    connection.execute(
        "INSERT INTO input VALUES ('a',16),('a.',17),('a',18),(NULL,NULL),('other',19)"
    )
    assert sorted(
        connection.execute(model(result, "range")).fetchall(), key=repr
    ) == sorted(
        [("A", 16.0), ("A", 18.0), ("missing", None), ("other", 19.0)], key=repr
    )


def test_native_type_conversion_invalid_numeric_to_null_and_real_to_integer_truncation():
    doc = parsed(
        {
            "input": source(
                "input", {"number": "string", "value": "real"}, nextNodes=[edge("cast")]
            ),
            "cast": node(
                "cast",
                "ChangeColumnType",
                fields={
                    "number": {"type": "real", "calc": None},
                    "value": {"type": "integer", "calc": None},
                },
            ),
        }
    )
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(number VARCHAR,value DOUBLE)")
    connection.execute("INSERT INTO input VALUES ('12.5',-9.7),('bad',3.8)")
    assert connection.execute(model(result, "cast")).fetchall() == [
        (12.5, -9),
        (None, 3),
    ]


def test_exact_remove_letters_uses_unicode_letter_category_and_global_replacement():
    doc = parsed(
        {
            "input": source("input", {"value": "string"}, nextNodes=[edge("clean")]),
            "clean": node(
                "clean",
                "QuickCalcColumn",
                columnName="value",
                calcExpressionType="RemoveLetters",
                expression="REGEXP_REPLACE([value], '[:Letter:]', '')",
            ),
        }
    )
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(value VARCHAR)")
    connection.execute("INSERT INTO input VALUES ('aé中12.3b'),(NULL)")
    assert connection.execute(model(result, "clean")).fetchall() == [("12.3",), (None,)]


@pytest.mark.parametrize(
    "formula,fields,expected",
    [
        (
            'DATE(DATEPARSE("dd/MM/yyyy",[text]))',
            {"text": "string"},
            [("2020-12-31",), (None,), ("2020-02-01",)],
        ),
        (
            'IF ISNULL([text]) = false THEN TRIM(SPLIT([text],"-",2)) ELSE "missing" END',
            {"text": "string"},
            [("b",), ("missing",), ("",)],
        ),
        ("IFNULL([number],0)", {"number": "integer"}, [(5,), (0,), (-2,)]),
        ("STR([flag])", {"flag": "boolean"}, [("1",), ("0",), (None,)]),
    ],
)
def test_typed_formula_ast_executes_verified_null_date_split_and_boolean_semantics(
    formula, fields, expected
):
    sql, _ = translate(formula, fields)
    connection = duckdb.connect()
    if "DATEPARSE" in formula:
        query = (
            "SELECT CAST(("
            + sql
            + ") AS VARCHAR) FROM (VALUES ('31/12/2020'),('29/02/2019'),('01/02/2020')) t(text)"
        )
    elif "SPLIT" in formula:
        query = "SELECT " + sql + " FROM (VALUES ('a- b -c'),(NULL),('one')) t(text)"
    elif "IFNULL" in formula:
        query = "SELECT " + sql + " FROM (VALUES (5),(NULL),(-2)) t(number)"
    else:
        query = "SELECT " + sql + " FROM (VALUES (TRUE),(FALSE),(NULL)) t(flag)"
    assert connection.execute(query).fetchall() == expected


def test_formula_quotes_are_literals_and_unsupported_syntax_fails_closed():
    expression, _ = translate('"x\' ; DROP TABLE victim; --"', {})
    connection = duckdb.connect()
    assert connection.execute("SELECT " + expression).fetchone() == (
        "x' ; DROP TABLE victim; --",
    )
    for text in [
        "[x]; DROP TABLE victim",
        "{FIXED : SUM([x])}",
        "WINDOW_SUM([x])",
        'SCRIPT_REAL("evil",[x])',
        "SELECT x",
        "DATE([x])",
        "STR(1.5)",
        'SPLIT([x],"-",-1)',
        "1+" * 2000 + "1",
    ]:
        with pytest.raises(ValueError):
            translate(text, {"x": "string"})


def test_unknown_operation_blocks_only_its_branch_and_empty_controls_are_not_sources():
    doc = parsed(
        {
            "input": source("input", {"x": "integer"}, nextNodes=[edge("unknown")]),
            "unknown": node("unknown", "ExternalScript"),
            "other": source("other", {"x": "integer"}),
            "empty": node(
                "empty",
                "Container",
                baseType="container",
                loomContainer={"nodes": {}},
                namespacesToInput={},
                namespacesToOutput={},
            ),
        }
    )
    result = generate(doc)
    assert result["validation"]["generated"] == 2
    assert result["validation"]["blocked"] == 1
    assert all(not n["id"].endswith(":empty") for n in result["pipeline"]["nodes"])


def test_bindings_validate_full_identity_identifier_exact_column_order_and_immutable_sources():
    doc = parsed({"input": source("input", {"a": "integer", "b": "string"})})
    original = json.dumps(doc, sort_keys=True)
    for mapping in [
        {"input": {"table": "good"}},
        {"transform:fixture:input": {"table": "good; DELETE FROM t"}},
        {"transform:fixture:input": {"table": "good", "columns": ["b", "a"]}},
        {"transform:fixture:input": {"table": "good", "unexpected": True}},
    ]:
        with pytest.raises(UsageError):
            apply_source_bindings(doc["assets"], mapping)
    result = generate(
        doc,
        {"transform:fixture:input": {"table": "schema.good", "columns": ["a", "b"]}},
    )
    assert json.dumps(doc, sort_keys=True) == original
    assert (
        result["pipeline"]["nodes"][0]["binding"]["origin"] == "explicit_source_binding"
    )
    assert generate(doc, {})["pipeline"]["nodes"][0]["error"].startswith(
        "source_binding_required"
    )


def test_deep_graph_and_cycle_remain_bounded_with_independent_branch():
    nodes = {"input": source("input", {"x": "integer"}, nextNodes=[edge("n0")])}
    for i in range(90):
        nodes[f"n{i}"] = node(
            f"n{i}",
            "AddColumn",
            columnName="x",
            expression="[x]+1",
            nextNodes=[edge(f"n{i + 1}")] if i < 89 else [],
        )
    result = generate(parsed(nodes))
    assert result["validation"]["generated"] == 65
    assert result["validation"]["blocked"] == 26
    assert any(
        "ancestor resource limit" in (n["error"] or "")
        for n in result["pipeline"]["nodes"]
    )
    nodes = {
        "a": node(
            "a", "AddColumn", columnName="x", expression="1", nextNodes=[edge("b")]
        ),
        "b": node(
            "b", "AddColumn", columnName="x", expression="1", nextNodes=[edge("a")]
        ),
        "input": source("input", {"x": "integer"}),
    }
    result = generate(parsed(nodes))
    assert result["validation"]["generated"] == 1
    assert result["validation"]["blocked"] == 2
    assert not result["validation"]["checks"]["acyclic"]


def test_offline_plan_is_checksummed_workspace_scoped_and_never_mutates_input(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    source_path = tmp_path / "input.tfl"
    payload = json.dumps(
        {"nodes": {"input": source("input", {"x": "integer"})}}
    ).encode()
    source_path.write_bytes(payload)
    result = plan_flow(
        flow=source_path,
        output="plan",
        flow_id="fixture",
        source_bindings={"transform:fixture:input": {"table": "synthetic"}},
    )
    assert result["generated"] == 1
    assert result["execution"] == "not_run"
    manifest = json.loads((tmp_path / "plan/plan-manifest.json").read_text())
    assert manifest["source_sha256"] == hashlib.sha256(payload).hexdigest()
    for name, checksum in manifest["files"].items():
        content = (tmp_path / "plan" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == checksum["sha256"]
    assert source_path.read_bytes() == payload
    with pytest.raises(UsageError):
        plan_flow(flow=source_path, output="../escape")
    with pytest.raises(UsageError):
        plan_flow(flow=source_path, output="plan")
    bad = tmp_path / "bindings.json"
    bad.write_text("[]")
    with pytest.raises(UsageError):
        read_bindings(bad)


def test_unknown_native_versions_and_output_ports_stay_local_blockers():
    unknown = source("future", {"x": "integer"})
    unknown["nodeType"] = ".v2.LoadCsv"
    doc = parsed(
        {
            "future": unknown,
            "input": source(
                "input",
                {"x": "integer"},
                nextNodes=[edge("derive", producer="Unmapped")],
            ),
            "derive": node("derive", "AddColumn", columnName="x", expression="[x]+1"),
            "good": source("good", {"x": "integer"}),
        }
    )
    result = generate(doc)
    assert result["validation"]["generated"] == 1
    errors = [n["error"] for n in result["pipeline"]["nodes"]]
    assert "unsupported native Prep operation version" in errors
    assert "unsupported native output namespace" in errors


def test_integer_division_is_real_before_native_integer_conversion_truncates():
    doc = parsed(
        {
            "input": source(
                "input", {"a": "integer", "b": "integer"}, nextNodes=[edge("ratio")]
            ),
            "ratio": node(
                "ratio",
                "AddColumn",
                columnName="ratio",
                expression="[a] / [b]",
                nextNodes=[edge("integer")],
            ),
            "integer": node(
                "integer",
                "ChangeColumnType",
                fields={"ratio": {"type": "integer", "calc": None}},
            ),
        }
    )
    result = generate(doc)
    ratio = next(a for a in doc["assets"] if a["id"].endswith(":ratio"))
    assert ratio["definition"]["output_schema"]["ratio"] == "real"
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(a INTEGER, b INTEGER)")
    connection.execute("INSERT INTO input VALUES (3,2),(-3,2),(NULL,2)")
    assert connection.execute(model(result, "ratio")).fetchall() == [
        (3, 2, 1.5),
        (-3, 2, -1.5),
        (None, 2, None),
    ]
    assert connection.execute(model(result, "integer")).fetchall() == [
        (3, 2, 1),
        (-3, 2, -1),
        (None, 2, None),
    ]


def test_generated_input_loader_actions_and_selected_fields_precede_union_provenance():
    def generated(identifier):
        return source(
            identifier,
            {"drop": "string", "keep": "integer", "unused": "string"},
            actions=[node("remove", "RemoveColumn", columnName="drop")],
            selectedFields=["keep"],
        )

    doc = parsed(
        {
            "files": node(
                "files",
                "LoadCsvInputUnion",
                fields=[],
                pathNameField="File Paths",
                generatedInputs=[
                    {"inputNode": generated("first"), "filePath": "first.csv"},
                    {"inputNode": generated("second"), "filePath": "second.csv"},
                ],
            )
        }
    )
    result = generate(
        doc,
        {
            "transform:fixture:files/generated/first": {"table": "first_input"},
            "transform:fixture:files/generated/second": {"table": "second_input"},
        },
    )
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE first_input(drop VARCHAR, keep INTEGER, unused VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE second_input(drop VARCHAR, keep INTEGER, unused VARCHAR)"
    )
    connection.execute("INSERT INTO first_input VALUES ('removed',7,'also removed')")
    connection.execute("INSERT INTO second_input VALUES ('removed',8,'also removed')")
    cursor = connection.execute(model(result, "files"))
    assert [column[0] for column in cursor.description] == ["keep", "File Paths"]
    assert cursor.fetchall() == [(7, "first.csv"), (8, "second.csv")]
    raw = next(a for a in doc["assets"] if a["id"].endswith("/generated/first"))
    assert raw["definition"]["operation"]["columns"] == ["drop", "keep", "unused"]
    assert result["validation"]["blocked"] == 0
    # A generated loader's unknown action must block its union branch.
    unknown = generated("first")
    unknown["actions"] = [node("script", "ExternalScript")]
    blocked = parsed(
        {
            "files": node(
                "files",
                "LoadCsvInputUnion",
                fields=[],
                pathNameField="File Paths",
                generatedInputs=[
                    {"inputNode": unknown, "filePath": "first.csv"},
                    {"inputNode": generated("second"), "filePath": "second.csv"},
                ],
            )
        }
    )
    output = generate(
        blocked,
        {
            "transform:fixture:files/generated/first": {"table": "first_input"},
            "transform:fixture:files/generated/second": {"table": "second_input"},
        },
    )
    assert (
        next(n for n in output["pipeline"]["nodes"] if n["id"].endswith(":files"))[
            "status"
        ]
        == "blocked"
    )
    assert any(
        "unsupported native Prep operation" in (n["error"] or "")
        for n in output["pipeline"]["nodes"]
    )


@pytest.mark.parametrize("bound", [9007199254740993, "9007199254740993"])
@pytest.mark.parametrize("exclude", [False, True])
def test_range_filter_preserves_exact_bigint_bounds_nulls_and_adjacent_values(
    bound, exclude
):
    doc = parsed(
        {
            "input": source("input", {"value": "integer"}, nextNodes=[edge("range")]),
            "range": node(
                "range",
                "RangeFilter",
                ranges={
                    "value": [
                        {
                            "startValue": bound,
                            "endValue": bound,
                            "includeStart": True,
                            "includeEnd": True,
                        }
                    ]
                },
                exclude=exclude,
            ),
        }
    )
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(value BIGINT)")
    connection.execute(
        "INSERT INTO input VALUES (9007199254740992),(9007199254740993),(9007199254740994),(NULL)"
    )
    expected = (
        [(9007199254740992,), (9007199254740994,), (None,)]
        if exclude
        else [(9007199254740993,)]
    )
    assert connection.execute(model(result, "range")).fetchall() == expected


def test_range_filter_retains_decimal_string_precision_and_rejects_decoded_float_bounds():
    bound = "0.12345678901234567890"
    doc = parsed(
        {
            "input": source("input", {"value": "real"}, nextNodes=[edge("range")]),
            "range": node(
                "range",
                "RangeFilter",
                ranges={
                    "value": [
                        {
                            "startValue": bound,
                            "endValue": bound,
                            "includeStart": True,
                            "includeEnd": True,
                        }
                    ]
                },
                exclude=False,
            ),
        }
    )
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(value DECIMAL(38,20))")
    connection.execute(
        "INSERT INTO input VALUES (0.12345678901234567889),(0.12345678901234567890),(0.12345678901234567891),(NULL)"
    )
    assert [
        str(row[0]) for row in connection.execute(model(result, "range")).fetchall()
    ] == [bound]
    doc = parsed(
        {
            "input": source("input", {"value": "real"}, nextNodes=[edge("range")]),
            "range": node(
                "range",
                "RangeFilter",
                ranges={
                    "value": [
                        {
                            "startValue": 0.12345678901234567890,
                            "endValue": 0.12345678901234567890,
                            "includeStart": True,
                            "includeEnd": True,
                        }
                    ]
                },
                exclude=False,
            ),
        }
    )
    result = generate(doc)
    row = next(n for n in result["pipeline"]["nodes"] if n["id"].endswith(":range"))
    assert row["status"] == "blocked"
    assert (
        row["error"]
        == "native numeric range requires an exact integer or decimal string bound"
    )


@pytest.mark.parametrize(
    "formula,expected",
    [
        (
            'IF NOT [x] > 0 THEN "nonpositive" ELSE "positive" END',
            [("nonpositive",), ("nonpositive",), ("positive",), ("positive",)],
        ),
        ("NOT [x] > 0 AND [a] OR [b]", [(True,), (False,), (False,), (None,)]),
        ("NOT ([x] > 0 AND [a]) OR [b]", [(True,), (True,), (False,), (None,)]),
        ("NOT NOT (-[x] < +0)", [(False,), (False,), (True,), (None,)]),
    ],
)
def test_tableau_not_precedence_boolean_logic_parentheses_and_unary_numbers(
    formula, expected
):
    expression, _ = translate(formula, {"x": "integer", "a": "boolean", "b": "boolean"})
    connection = duckdb.connect()
    assert (
        connection.execute(
            "SELECT "
            + expression
            + " FROM (VALUES (-1,TRUE,FALSE),(0,FALSE,FALSE),(1,TRUE,FALSE),(NULL,TRUE,FALSE)) t(x,a,b)"
        ).fetchall()
        == expected
    )
    for invalid in ["NOT [x]", "[x] AND [a]", "[a] OR [x]", "-[a]", "+[a]"]:
        with pytest.raises(ValueError, match="operand"):
            translate(invalid, {"x": "integer", "a": "boolean"})
