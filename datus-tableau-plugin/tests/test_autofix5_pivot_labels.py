import duckdb
import pytest
from datus_tableau_plugin.transforms import generate_transforms
from test_transforms import model, node


def operation():
    return {
        "type": "pivot",
        "direction": "columns_to_rows",
        "index": ["id"],
        "values": ["a", "b"],
        "names_to": "measure",
        "values_to": "amount",
        "include_nulls": True,
    }


@pytest.mark.parametrize(
    "labels", [[], ["only_one"], ["one", "two", "extra"], None, "ab", [1, "two"]]
)
def test_bad_pivot_label_shape_blocks_locally_without_aborting_other_models(labels):
    pivot = operation()
    pivot["labels"] = labels
    result = generate_transforms(
        [
            node("source", {"type": "source", "table": "synthetic"}),
            node("pivot", pivot, ("source",)),
            node("downstream", {"type": "output"}, ("pivot",)),
            node("independent", {"type": "source", "sql": "SELECT 7 AS id"}),
        ],
        [],
    )
    assert (
        result["validation"]["generated"] == 2 and result["validation"]["blocked"] == 2
    )
    row = next(n for n in result["pipeline"]["nodes"] if n["id"] == "pivot")
    assert row["status"] == "blocked" and "pivot labels" in row["error"]
    assert duckdb.connect().execute(model(result, "independent")).fetchall() == [(7,)]


@pytest.mark.parametrize(
    "labels,expected",
    [
        (None, [(1, "a", 2), (1, "b", None)]),
        (["", "second"], [(1, "", 2), (1, "second", None)]),
    ],
)
def test_omitted_labels_default_and_valid_empty_string_labels_execute(labels, expected):
    pivot = operation()
    if labels is not None:
        pivot["labels"] = labels
    result = generate_transforms(
        [
            node("source", {"type": "source", "table": "synthetic"}),
            node("pivot", pivot, ("source",)),
        ],
        [],
    )
    connection = duckdb.connect()
    connection.execute("CREATE TABLE synthetic(id INTEGER,a INTEGER,b INTEGER)")
    connection.execute("INSERT INTO synthetic VALUES (1,2,NULL)")
    assert connection.execute(model(result, "pivot")).fetchall() == expected
