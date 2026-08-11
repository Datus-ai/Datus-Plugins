from pathlib import Path

import pytest

from tests.e2e.harness.runner import run_repeated
from tests.e2e.harness.schema import load_run_config, load_workflow


@pytest.mark.llm_e2e
def test_selected_workflow(request, repo_root):
    if not request.config.getoption("--run-live"):
        pytest.skip("live provisioning and LLM calls require --run-live")
    name = request.config.getoption("--workflow")
    config_path = request.config.getoption("--run-config")
    if not name or not config_path:
        pytest.fail("--workflow and --run-config are required with --run-live")
    workflow_path = Path(__file__).parent / "workflows" / name / "workflow.yml"
    workflow = load_workflow(workflow_path)
    if "reference" in workflow.tags:
        pytest.skip("reference workflow is a design example until its environment fixture is promoted")
    results = run_repeated(workflow, load_run_config(config_path), repo_root=repo_root)
    failures = [result for result in results if result["status"] != "PASS" or not result["quality_passed"]]
    assert not failures, failures
