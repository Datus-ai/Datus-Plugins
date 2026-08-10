from pathlib import Path

import pytest

from tests.e2e.harness.schema import load_workflow


WORKFLOWS = sorted((Path(__file__).parent / "workflows").glob("*/workflow.yml"))


@pytest.mark.e2e
@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.parent.name)
def test_workflow_contract(path):
    workflow = load_workflow(path)
    assert workflow.path == path.resolve()
    assert workflow.oracles
    assert workflow.outputs
