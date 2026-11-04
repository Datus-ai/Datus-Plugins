import duckdb
import pytest
from datus_tableau_plugin.bindings import apply_source_bindings
from datus_tableau_plugin.transforms import generate_transforms
from test_prep_native import edge, model, node, parsed, source


def test_native_custom_sql_is_retained_but_bound_table_cannot_drop_where_clause():
    sql = "SELECT id FROM raw_orders WHERE id > 0"
    doc = parsed(
        {
            "sql": node(
                "sql",
                "LoadSql",
                fields=[{"name": "id", "type": "integer"}],
                customSql=sql,
                nextNodes=[edge("out")],
            ),
            "out": node("out", "Output"),
            "independent": source("independent", {"id": "integer"}),
        }
    )
    assert any(
        q["text"] == sql
        and q["origin"] == "source"
        and q["language"] == "sql"
        and q["asset_id"] == "transform:fixture:sql"
        for q in doc["queries"]
    )
    assets = apply_source_bindings(
        doc["assets"],
        {
            "transform:fixture:sql": {"table": "would_ignore_where"},
            "transform:fixture:independent": {"table": "independent"},
        },
    )
    result = generate_transforms(assets, doc["dependencies"])
    assert (
        result["validation"]["generated"] == 1 and result["validation"]["blocked"] == 2
    )
    sql_row = next(
        n for n in result["pipeline"]["nodes"] if n["id"] == "transform:fixture:sql"
    )
    assert sql_row["status"] == "blocked" and "custom SQL" in sql_row["error"]
    assert (
        next(
            n for n in result["pipeline"]["nodes"] if n["id"] == "transform:fixture:out"
        )["status"]
        == "blocked"
    )
    assert all("would_ignore_where" not in text for text in result["models"].values())
    connection = duckdb.connect()
    connection.execute("CREATE TABLE independent(id INTEGER)")
    connection.execute("INSERT INTO independent VALUES (7)")
    assert connection.execute(model(result, "independent")).fetchall() == [(7,)]


@pytest.mark.parametrize("query", [None, "", "   "])
def test_native_loadsql_without_nonempty_query_keeps_explicit_table_binding(query):
    doc = parsed(
        {
            "sql": node(
                "sql",
                "LoadSql",
                fields=[{"name": "id", "type": "integer"}],
                customSql=query,
            )
        }
    )
    assets = apply_source_bindings(
        doc["assets"], {"transform:fixture:sql": {"table": "synthetic"}}
    )
    result = generate_transforms(assets, doc["dependencies"])
    assert (
        result["validation"]["blocked"] == 0 and result["validation"]["generated"] == 1
    )
    assert doc["queries"] == []
    connection = duckdb.connect()
    connection.execute("CREATE TABLE synthetic(id INTEGER)")
    connection.execute("INSERT INTO synthetic VALUES (9)")
    assert connection.execute(model(result, "sql")).fetchall() == [(9,)]
