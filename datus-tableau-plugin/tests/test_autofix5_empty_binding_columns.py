import copy
import pytest
from datus_tableau_plugin.bindings import apply_source_bindings
from datus_tableau_plugin.errors import UsageError
from test_transforms import node


@pytest.mark.parametrize("native_columns", [None, []])
def test_explicit_empty_binding_columns_rejected_without_mutating_assets(
    native_columns,
):
    operation = {"type": "source", "binding_required": True}
    if native_columns is not None:
        operation["columns"] = native_columns
    assets = [node("transform:fixture:source", operation)]
    original = copy.deepcopy(assets)
    with pytest.raises(UsageError, match="nonempty"):
        apply_source_bindings(
            assets, {"transform:fixture:source": {"table": "synthetic", "columns": []}}
        )
    assert assets == original


def test_omitted_columns_and_valid_nonempty_columns_keep_existing_contract():
    assets = [
        node(
            "transform:fixture:source",
            {"type": "source", "binding_required": True, "columns": ["id"]},
        )
    ]
    for binding in [{"table": "synthetic"}, {"table": "synthetic", "columns": ["id"]}]:
        bound = apply_source_bindings(assets, {"transform:fixture:source": binding})
        assert bound[0]["definition"]["operation"]["columns"] == ["id"]
