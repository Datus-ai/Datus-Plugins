from pathlib import Path

import pytest
import yaml

from tests.e2e.harness.schema import load_workflow


WORKFLOWS = sorted((Path(__file__).parent / "workflows").glob("*/workflow.yml"))


@pytest.mark.e2e
@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.parent.name)
def test_workflow_contract(path):
    workflow = load_workflow(path)
    assert workflow.path == path.resolve()
    assert workflow.oracles
    assert workflow.outputs


def test_flink2paimon_datagen_uses_flink2_builtin_sql_driver():
    workflow_dir = Path(__file__).parent / "workflows/flink2paimon-datagen"
    lock = yaml.safe_load((workflow_dir / "environment.lock.yml").read_text(encoding="utf-8"))
    prompt = (workflow_dir / "prompt.md").read_text(encoding="utf-8")
    workflow = yaml.safe_load((workflow_dir / "workflow.yml").read_text(encoding="utf-8"))
    runner_dir = Path(__file__).parent / "environments/flink-paimon-runner"
    dockerfile = (runner_dir / "Dockerfile").read_text(encoding="utf-8")

    assert lock["flink"] == "2.0.2"
    assert lock["paimon"] == "1.4.1"
    assert "org.apache.flink.table.runtime.application.SqlDriver" in prompt
    assert "file:///opt/flink/usrlib/job.sql" in prompt
    assert "Omit `spec.job.jarURI` entirely" in prompt
    assert "`spec.mode: standalone`" in prompt
    assert not (runner_dir / "src/main/java/ai/datus/e2e/SqlFileRunner.java").exists()
    assert "e2e-verifier.jar /opt/flink/lib/e2e-verifier.jar" in dockerfile
    assert "/opt/flink/usrlib" not in dockerfile

    file_oracle = workflow["spec"]["oracles"][0]["config"]
    required = file_oracle["content"]["deploy/flink/*/*.yaml"]
    forbidden = file_oracle["notContent"]["deploy/flink/*/*.yaml"]
    assert any("SqlDriver" in expression for expression in required)
    assert any("standalone" in expression for expression in required)
    assert any("jarURI" in expression for expression in forbidden)
    assert any("pipeline" in expression for expression in forbidden)
