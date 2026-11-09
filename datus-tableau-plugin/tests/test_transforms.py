from __future__ import annotations

import sqlite3

import pytest

from datus_tableau_plugin.transforms import generate_transforms


def node(name, operation, inputs=(), status="ok"):
    return {
        "id": name,
        "kind": "transform",
        "name": name,
        "source": {},
        "status": status,
        "definition": {"operation": operation, "inputs": list(inputs)},
    }


def model(result, asset_id):
    row = next(item for item in result["pipeline"]["nodes"] if item["id"] == asset_id)
    return result["models"][row["file"]]


def test_generated_join_pivot_aggregate_is_executable_and_keeps_dependencies():
    assets = [
        node("orders", {"type": "source", "table": "orders"}),
        node("regions", {"type": "source", "table": "regions"}),
        node(
            "joined",
            {
                "type": "join",
                "join_type": "LEFT",
                "condition": "l.region_id = r.id",
                "expression_language": "sql",
                "projection": [
                    {"side": "right", "column": "name", "name": "region"},
                    {"side": "left", "column": "sales"},
                    {"side": "left", "column": "cost"},
                ],
            },
            ("orders", "regions"),
        ),
        node(
            "pivoted",
            {
                "type": "pivot",
                "direction": "columns_to_rows",
                "index": ["region"],
                "values": ["sales", "cost"],
                "names_to": "measure",
                "values_to": "amount",
                "include_nulls": True,
            },
            ("joined",),
        ),
        node(
            "aggregated",
            {
                "type": "aggregate",
                "group_by": ["region", "measure"],
                "aggregations": [{"name": "total", "expression": "SUM(amount)"}],
                "expression_language": "sql",
            },
            ("pivoted",),
        ),
    ]
    result = generate_transforms(assets, [], dialect="sqlite")
    assert result["validation"]["blocked"] == 0
    assert result["pipeline"]["order"].index("orders") < result["pipeline"][
        "order"
    ].index("joined")
    with sqlite3.connect(":memory:") as database:
        database.executescript(
            "CREATE TABLE orders(region_id INTEGER,sales INTEGER,cost INTEGER); CREATE TABLE regions(id INTEGER,name TEXT); INSERT INTO orders VALUES(1,10,4),(1,20,7),(2,5,2); INSERT INTO regions VALUES(1,'East'),(2,'West');"
        )
        rows = database.execute(model(result, "aggregated")).fetchall()
    assert sorted(rows) == [
        ("East", "cost", 11),
        ("East", "sales", 30),
        ("West", "cost", 2),
        ("West", "sales", 5),
    ]
    assert result["validation"]["execution"] == "not_run"
    assert result["validation"]["checks"]["data_types"] == "not_verified"
    assert all(item["origin"] == "derived" for item in result["pipeline"]["nodes"])


def test_unknown_native_node_blocks_descendants_but_not_independent_branch():
    result = generate_transforms(
        [
            node("good", {"type": "source", "table": "orders"}),
            node(
                "script",
                {"type": "unsupported", "native_type": ".v1.PythonScript"},
                status="unsupported",
            ),
            node("dependent", {"type": "output"}, ("script",)),
        ],
        [],
    )
    assert result["validation"]["generated"] == 1
    assert result["validation"]["blocked"] == 2
    assert "orders" in model(result, "good")


@pytest.mark.parametrize(
    "sql", ["DELETE FROM orders", "SELECT 1; DROP TABLE orders", "SELECT 1 INTO copied"]
)
def test_source_sql_writes_are_not_processing_models(sql):
    result = generate_transforms([node("source", {"type": "source", "sql": sql})], [])
    assert not result["models"]
    assert result["validation"]["blocked"] == 1


def test_native_tableau_formula_is_not_silently_executed_as_sql():
    result = generate_transforms(
        [
            node("s", {"type": "source", "table": "orders"}),
            node("f", {"type": "filter", "condition": "[Sales] > 10"}, ("s",)),
        ],
        [],
    )
    assert result["validation"]["generated"] == 1
    assert (
        "explicit SQL-language"
        in next(row for row in result["pipeline"]["nodes"] if row["id"] == "f")["error"]
    )


def test_union_requires_explicit_column_alignment():
    result = generate_transforms(
        [
            node("a", {"type": "source", "table": "a"}),
            node("b", {"type": "source", "table": "b"}),
            node("u", {"type": "union"}, ("a", "b")),
        ],
        [],
    )
    assert result["validation"]["blocked"] == 1


def test_cycle_and_missing_dependency_do_not_emit_bad_models():
    result = generate_transforms(
        [node("a", {"type": "output"}, ("b",)), node("b", {"type": "output"}, ("a",))],
        [],
    )
    assert not result["models"]
    assert result["pipeline"]["cycle"]
    missing = generate_transforms([node("a", {"type": "output"}, ("missing",))], [])
    assert not missing["models"]
    assert "missing transform dependency" in missing["pipeline"]["nodes"][0]["error"]


@pytest.mark.parametrize("status", ["partial", "error", "unsupported"])
def test_incomplete_source_is_never_emitted_as_a_successful_model(status):
    result = generate_transforms(
        [node("s", {"type": "source", "table": "sales"}, status=status)], []
    )
    assert not result["models"]
    assert result["validation"]["blocked"] == 1


def test_unsupported_query_translation_blocks_dependents_without_dropping_sampling():
    result = generate_transforms(
        [
            node(
                "sampled",
                {
                    "type": "source",
                    "sql": "SELECT * FROM t TABLESAMPLE BERNOULLI (10)",
                    "source_dialect": "postgres",
                },
            ),
            node("dependent", {"type": "output"}, ("sampled",)),
            node(
                "independent",
                {
                    "type": "source",
                    "sql": "SELECT 7 AS value",
                    "source_dialect": "postgres",
                },
            ),
        ],
        [],
        dialect="sqlite",
    )
    rows = {row["id"]: row for row in result["pipeline"]["nodes"]}
    assert rows["sampled"]["status"] == "blocked"
    assert rows["sampled"]["error"]
    assert rows["dependent"]["status"] == "blocked"
    assert rows["independent"]["status"] == "generated"
    assert result["validation"]["blocked"] == 2
    assert result["validation"]["generated"] == 1
    with sqlite3.connect(":memory:") as database:
        assert database.execute(model(result, "independent")).fetchall() == [(7,)]


def test_unsupported_scalar_translation_blocks_dependents_without_losing_array_dimension():
    result = generate_transforms(
        [
            node("input", {"type": "source", "table": "orders"}),
            node(
                "length",
                {
                    "type": "derive",
                    "expression": "ARRAY_LENGTH(my_values, dimension)",
                    "expression_language": "sql",
                    "source_dialect": "postgres",
                    "columns": ["my_values", "dimension"],
                    "column_name": "array_size",
                },
                ("input",),
            ),
            node("dependent", {"type": "output"}, ("length",)),
        ],
        [],
        dialect="sqlite",
    )
    rows = {row["id"]: row for row in result["pipeline"]["nodes"]}
    assert rows["length"]["status"] == "blocked"
    assert "ARRAY_LENGTH" in rows["length"]["error"]
    assert rows["dependent"]["status"] == "blocked"
    assert rows["input"]["status"] == "generated"
    assert result["validation"]["blocked"] == 2
    assert result["validation"]["generated"] == 1
