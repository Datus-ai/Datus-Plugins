import duckdb
import pytest
from datus_tableau_plugin.formula import translate
from test_prep_native import edge, generate, model, node, parsed, source


@pytest.mark.parametrize(
    "formula, expected_type, expected",
    [
        ("IFNULL([value], 0.5)", "real", 0.5),
        ("IFNULL([value], 1.5)", "real", 1.5),
        ("IFNULL(1.5, [value])", "real", 1.5),
        ("IFNULL(NULL, 1.5)", "real", 1.5),
        ("IFNULL(NULL, [integer])", "integer", 4),
        ('IFNULL(NULL, "fallback")', "string", "fallback"),
        ("IFNULL(NULL, NULL)", "null", None),
    ],
)
def test_ifnull_infers_widest_nonnull_type_and_executes_null_first(
    formula, expected_type, expected
):
    expression, dtype = translate(formula, {"value": "integer", "integer": "integer"})
    assert dtype == expected_type
    assert duckdb.connect().execute(
        "SELECT " + expression + " FROM (VALUES (NULL,4)) t(value,integer)"
    ).fetchone() == (expected,)
    with pytest.raises(ValueError, match="incompatible"):
        translate('IFNULL([value], "incompatible")', {"value": "integer"})


@pytest.mark.parametrize("fallback,expected_integer", [(0.5, 0), (1.5, 1)])
def test_ifnull_mixed_numeric_result_is_truncated_by_later_integer_conversion(
    fallback, expected_integer
):
    doc = parsed(
        {
            "input": source(
                "input", {"value": "integer"}, nextNodes=[edge("fallback")]
            ),
            "fallback": node(
                "fallback",
                "AddColumn",
                columnName="filled",
                expression=f"IFNULL([value], {fallback})",
                nextNodes=[edge("integer")],
            ),
            "integer": node(
                "integer",
                "ChangeColumnType",
                fields={"filled": {"type": "integer", "calc": None}},
            ),
        }
    )
    derived = next(
        asset for asset in doc["assets"] if asset["id"] == "transform:fixture:fallback"
    )
    assert derived["definition"]["output_schema"] == {
        "value": "integer",
        "filled": "real",
    }
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(value INTEGER)")
    connection.execute("INSERT INTO input VALUES (NULL),(4)")
    assert connection.execute(model(result, "fallback")).fetchall() == [
        (None, fallback),
        (4, 4),
    ]
    assert connection.execute(model(result, "integer")).fetchall() == [
        (None, expected_integer),
        (4, 4),
    ]
