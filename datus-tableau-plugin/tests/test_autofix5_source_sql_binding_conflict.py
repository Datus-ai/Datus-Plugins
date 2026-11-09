import duckdb
import pytest
from datus_tableau_plugin.transforms import generate_transforms
from test_transforms import model, node


@pytest.mark.parametrize(
    "binding", [None, {"origin": "explicit_source_binding", "table": "ignored"}]
)
def test_sql_shortcut_cannot_bypass_required_source_binding_or_ignore_bound_table(
    binding,
):
    operation = {
        "type": "source",
        "binding_required": True,
        "sql": "SELECT 1 AS id",
        "columns": ["id"],
    }
    if binding:
        operation.update(binding=binding, table="ignored")
    result = generate_transforms(
        [
            node("source", operation),
            node("dependent", {"type": "output"}, ("source",)),
            node("independent", {"type": "source", "sql": "SELECT 7 AS id"}),
        ],
        [],
    )
    assert (
        result["validation"]["generated"] == 1 and result["validation"]["blocked"] == 2
    )
    source = next(n for n in result["pipeline"]["nodes"] if n["id"] == "source")
    assert ("conflicts" if binding else "source_binding_required") in source["error"]
    assert duckdb.connect().execute(model(result, "independent")).fetchall() == [(7,)]


def test_plain_sql_and_plain_table_sources_remain_executable():
    result = generate_transforms(
        [
            node("sql", {"type": "source", "sql": "SELECT 3 AS id"}),
            node("table", {"type": "source", "table": "synthetic"}),
        ],
        [],
    )
    assert result["validation"]["blocked"] == 0
    connection = duckdb.connect()
    connection.execute("CREATE TABLE synthetic(id INTEGER)")
    connection.execute("INSERT INTO synthetic VALUES (5)")
    assert connection.execute(model(result, "sql")).fetchall() == [(3,)]
    assert connection.execute(model(result, "table")).fetchall() == [(5,)]
