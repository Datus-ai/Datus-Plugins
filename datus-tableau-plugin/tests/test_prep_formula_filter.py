"""Native formula filters preserve row semantics and fail closed.

The end-to-end fixture is a downloaded, successfully run Cloud flow; only its
PublishExtract destination metadata is anonymized. Every native transform,
including the Completed filter and container ports, is retained verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from datus_tableau_plugin import cli
from test_prep_native import edge, generate, model, node, parsed, source


def filter_flow(expression):
    return parsed(
        {
            "input": source(
                "input",
                {"Status": "string", "Amount": "integer"},
                nextNodes=[edge("filter")],
            ),
            "filter": node(
                "filter",
                "FilterOperation",
                filterExpression=expression,
                nextNodes=[edge("output")],
            ),
            "output": node("output", "PublishExtract"),
        }
    )


def test_formula_filter_keeps_only_true_rows_and_preserves_schema_and_provenance():
    expression = "[Status] = 'Completed' AND [Amount] >= 0"
    document = filter_flow(expression)
    result = generate(document)
    assert result["validation"]["blocked"] == 0
    operation = next(a for a in document["assets"] if a["id"].endswith(":filter"))[
        "definition"
    ]["operation"]
    assert operation["original_expression"] == expression
    assert operation["original_expression_language"] == "tableau"
    assert operation["expression_language"] == "sql"
    with duckdb.connect() as connection:
        connection.execute('CREATE TABLE input("Status" VARCHAR, "Amount" INTEGER)')
        connection.execute(
            "INSERT INTO input VALUES ('Completed',2),('Cancelled',100),"
            "(NULL,3),('Completed',NULL),('Completed',-1)"
        )
        cursor = connection.execute(model(result, "output"))
        assert [c[0] for c in cursor.description] == ["Status", "Amount"]
        assert cursor.fetchall() == [("Completed", 2)]


@pytest.mark.parametrize(
    "expression",
    [
        None,
        "",
        "[Status] =",
        "[Absent] = 'Completed'",
        "[Amount] + 1",
        "[Status]",
        "NULL",
        "RAWSQL_BOOL('TRUE')",
        "[Status] = 'Completed'; SELECT 1",
    ],
)
def test_missing_malformed_unsupported_or_nonboolean_filter_blocks_descendants(
    expression,
):
    result = generate(filter_flow(expression))
    rows = {r["id"].rsplit(":", 1)[-1]: r for r in result["pipeline"]["nodes"]}
    assert rows["input"]["status"] == "generated"
    assert rows["filter"]["status"] == rows["output"]["status"] == "blocked"
    assert rows["filter"]["error"]
    assert "file" not in rows["filter"] and "file" not in rows["output"]
    assert result["validation"]["blocked"] == 2


@pytest.mark.parametrize(
    "predicate", ["[Amount]", "[Status]", "DATEPARSE('dd/MM/yyyy', '01/01/2026')"]
)
@pytest.mark.parametrize("branch", ["IF", "ELSEIF"])
def test_filter_rejects_nonboolean_if_and_elseif_predicates(predicate, branch):
    expression = (
        f"IF {predicate} THEN TRUE ELSE FALSE END"
        if branch == "IF"
        else f"IF FALSE THEN FALSE ELSEIF {predicate} THEN TRUE ELSE FALSE END"
    )
    result = generate(filter_flow(expression))
    rows = {r["id"].rsplit(":", 1)[-1]: r for r in result["pipeline"]["nodes"]}
    assert rows["filter"]["status"] == rows["output"]["status"] == "blocked"
    assert "IF condition must be boolean" in rows["filter"]["error"]
    assert "file" not in rows["filter"] and "file" not in rows["output"]


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("IF [Amount] > 0 THEN TRUE ELSE FALSE END", [("Completed", 1)]),
        (
            "IF FALSE THEN FALSE ELSEIF [Amount] > 0 THEN TRUE ELSE FALSE END",
            [("Completed", 1)],
        ),
        (
            "IF NULL THEN FALSE ELSE [Status] = 'Completed' END",
            [("Completed", 1), ("Completed", None)],
        ),
        (
            "IF FALSE THEN FALSE ELSEIF NULL THEN FALSE ELSE [Status] = 'Completed' END",
            [("Completed", 1), ("Completed", None)],
        ),
    ],
)
def test_filter_if_boolean_and_null_conditions_execute(expression, expected):
    result = generate(filter_flow(expression))
    assert result["validation"]["blocked"] == 0
    with duckdb.connect() as connection:
        connection.execute('CREATE TABLE input("Status" VARCHAR, "Amount" INTEGER)')
        connection.execute(
            "INSERT INTO input VALUES ('Completed',1),('Cancelled',0),('Completed',NULL)"
        )
        assert connection.execute(model(result, "output")).fetchall() == expected


@pytest.mark.parametrize("function", [None, "", "GroupBy", "YEAR", "SUM"])
def test_native_aggregate_accepts_only_plain_grouping_markers(function):
    document = parsed(
        {
            "input": source(
                "input",
                {"Status": "string", "Amount": "integer"},
                nextNodes=[edge("aggregate")],
            ),
            "aggregate": node(
                "aggregate",
                "Aggregate",
                groupByFields=[{"columnName": "Status", "function": function}],
                aggregateFields=[{"columnName": "Amount", "function": "SUM"}],
            ),
        }
    )
    result = generate(document)
    row = next(r for r in result["pipeline"]["nodes"] if r["id"].endswith(":aggregate"))
    if function in (None, "", "GroupBy"):
        assert row["status"] == "generated"
        with duckdb.connect() as connection:
            connection.execute('CREATE TABLE input("Status" VARCHAR, "Amount" INTEGER)')
            connection.execute(
                "INSERT INTO input VALUES ('Completed',2),('Completed',3)"
            )
            assert connection.execute(model(result, "aggregate")).fetchall() == [
                ("Completed", 5)
            ]
    else:
        assert row["status"] == "blocked"
        assert row["error"] == "unsupported native aggregation grouping"


def test_downloaded_native_flow_managed_plan_executes_csv_totals(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    fixtures = Path(__file__).parent / "fixtures/native-filter-flow"
    payload = (fixtures / "flow.tfl").read_bytes()
    flow = json.loads(payload)
    bindings = {
        f"transform:cloud-demo:{n['id']}": {
            "table": n["name"],
            "columns": [f["name"] for f in n["fields"]],
        }
        for n in flow["nodes"].values()
        if n["nodeType"] == ".v1.LoadCsv"
    }
    (tmp_path / "bindings.json").write_text(json.dumps(bindings))
    assert (
        cli.main(
            [
                "plan",
                "--flow",
                str(fixtures / "flow.tfl"),
                "--flow-id",
                "cloud-demo",
                "--source-bindings",
                "bindings.json",
                "--output",
                "plan",
                "--dialect",
                "duckdb",
            ],
            {},
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert (summary["generated"], summary["blocked"]) == (7, 0)
    pipeline = json.loads((tmp_path / "plan/pipeline.json").read_text())
    output = next(n for n in pipeline["nodes"] if n["operation"] == "output")
    sql = (tmp_path / "plan" / output["file"]).read_text()
    with duckdb.connect() as connection:
        for table in ("demo_orders", "demo_regions"):
            connection.execute(
                f'CREATE TABLE "{table}" AS SELECT * FROM read_csv_auto(?)',
                [str(fixtures / f"{table}.csv")],
            )
        cursor = connection.execute(sql)
        assert [c[0] for c in cursor.description] == [
            "Region",
            "Market",
            "Amount",
            "Cost",
            "NetSales",
            "Quantity",
        ]
        rows = sorted(cursor.fetchall())
        assert rows == [
            ("East", "APAC", 100, 60, 40, 3),
            ("North", "NA", 200, 90, 110, 3),
            ("South", "SA", 200, 100, 100, 4),
        ]
        assert tuple(sum(r[i] for r in rows) for i in range(2, 6)) == (
            500,
            250,
            250,
            10,
        )
    assert (fixtures / "flow.tfl").read_bytes() == payload
