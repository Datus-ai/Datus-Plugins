import duckdb
import pytest
from test_prep_native import generate, model, node, parsed, source


@pytest.mark.parametrize("with_actions", [False, True])
def test_generated_file_paths_collision_blocks_locally_and_preserves_independent_branch(
    with_actions,
):
    def generated(identifier):
        kwargs = (
            {"actions": [node("remove", "RemoveColumn", columnName="unused")]}
            if with_actions
            else {}
        )
        return source(
            identifier, {"File Paths": "string", "unused": "integer"}, **kwargs
        )

    doc = parsed(
        {
            "files": node(
                "files",
                "LoadCsvInputUnion",
                fields=[],
                pathNameField="File Paths",
                generatedInputs=[
                    {"inputNode": generated("first"), "filePath": "literal-first.csv"},
                    {
                        "inputNode": generated("second"),
                        "filePath": "literal-second.csv",
                    },
                ],
            ),
            "independent": source("independent", {"id": "integer"}),
        }
    )
    result = generate(doc)
    row = next(n for n in result["pipeline"]["nodes"] if n["id"].endswith(":files"))
    assert row["status"] == "blocked"
    assert any("collid" in (n["error"] or "") for n in result["pipeline"]["nodes"])
    assert result["validation"]["generated"] == (5 if with_actions else 1)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE independent(id INTEGER)")
    connection.execute("INSERT INTO independent VALUES (7)")
    assert connection.execute(model(result, "independent")).fetchall() == [(7,)]
