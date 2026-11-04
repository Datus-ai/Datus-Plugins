import duckdb
import pytest
from datus_tableau_plugin.formula import translate
from test_prep_native import edge, generate, model, node, parsed, source


@pytest.mark.parametrize(
    "formula, expected_type, expected",
    [
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


def test_ifnull_mixed_numeric_result_is_truncated_by_later_integer_conversion():
    doc = parsed(
        {
            "input": source(
                "input", {"value": "integer"}, nextNodes=[edge("fallback")]
            ),
            "fallback": node(
                "fallback",
                "AddColumn",
                columnName="filled",
                expression="IFNULL([value], 1.5)",
                nextNodes=[edge("integer")],
            ),
            "integer": node(
                "integer",
                "ChangeColumnType",
                fields={"filled": {"type": "integer", "calc": None}},
            ),
        }
    )
    result = generate(doc)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE input(value INTEGER)")
    connection.execute("INSERT INTO input VALUES (NULL),(4)")
    assert connection.execute(model(result, "integer")).fetchall() == [
        (None, 1),
        (4, 4),
    ]
