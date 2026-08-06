"""Validate the Flink Operator templates the skill file carries inline."""

from __future__ import annotations

from pathlib import Path

import yaml
from skill_blocks import blocks, languages, render

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "datus_flink_plugin" / "skills" / "flink-k8s-operator" / "SKILL.md"
FIXTURES = ROOT / "tests" / "fixtures"

VALUES = {
    "__FLINK_API_VERSION__": "flink.apache.org/v1beta1",
    "__NAME__": "orders",
    "__NAMESPACE__": "analytics",
    "__IMAGE__": "registry.example.com/flink/orders:abc123",
    "__IMAGE_PULL_POLICY__": "IfNotPresent",
    "__FLINK_VERSION__": "v1_20",
    "__SERVICE_ACCOUNT__": "flink",
    "__TASK_SLOTS__": "2",
    "__JOB_MANAGER_MEMORY__": "1024m",
    "__JOB_MANAGER_CPU__": "1",
    "__TASK_MANAGER_MEMORY__": "2048m",
    "__TASK_MANAGER_CPU__": "1",
    "__JOB_URI__": "local:///opt/flink/usrlib/job.jar",
    "__ENTRY_CLASS__": "com.example.OrdersJob",
    "__PARALLELISM__": "2",
    "__UPGRADE_MODE__": "savepoint",
    "__SESSION_CLUSTER_NAME__": "shared-session",
    "__JOB_NAME__": "orders",
    "__OPERATOR_ACCESSIBLE_JOB_URI__": "https://artifacts.example.com/orders.jar",
    "__SNAPSHOT_NAME__": "orders-manual-1",
    "__FlinkDeployment_OR_FlinkSessionJob__": "FlinkDeployment",
    "__JOB_RESOURCE_NAME__": "orders",
}


def render_yaml(name: str) -> dict:
    return yaml.safe_load(render(blocks(SKILL)[name], VALUES))


def test_skill_file_carries_every_template_inline():
    assert set(blocks(SKILL)) == {
        "flinkdeployment-application.yaml",
        "flinkdeployment-session.yaml",
        "flinksessionjob.yaml",
        "flinkstatesnapshot.yaml",
        "Dockerfile.jvm",
        "Dockerfile.pyflink",
    }
    assert languages(SKILL) == {
        "flinkdeployment-application.yaml": "yaml",
        "flinkdeployment-session.yaml": "yaml",
        "flinksessionjob.yaml": "yaml",
        "flinkstatesnapshot.yaml": "yaml",
        "Dockerfile.jvm": "dockerfile",
        "Dockerfile.pyflink": "dockerfile",
    }


def test_application_template():
    data = render_yaml("flinkdeployment-application.yaml")
    assert data["apiVersion"] == "flink.apache.org/v1beta1"
    assert data["kind"] == "FlinkDeployment"
    assert data["metadata"]["namespace"] == "analytics"
    assert data["spec"]["job"] == {
        "jarURI": "local:///opt/flink/usrlib/job.jar",
        "entryClass": "com.example.OrdersJob",
        "args": [],
        "parallelism": 2,
        "upgradeMode": "savepoint",
        "state": "running",
    }


def test_session_templates_link_by_deployment_name():
    cluster = render_yaml("flinkdeployment-session.yaml")
    job = render_yaml("flinksessionjob.yaml")
    assert cluster["kind"] == "FlinkDeployment"
    assert "job" not in cluster["spec"]
    assert job["kind"] == "FlinkSessionJob"
    assert job["spec"]["deploymentName"] == cluster["metadata"]["name"]
    assert job["spec"]["job"]["jarURI"].startswith("https://")


def test_snapshot_template_preserves_savepoint_data_by_default():
    data = render_yaml("flinkstatesnapshot.yaml")
    assert data["kind"] == "FlinkStateSnapshot"
    assert data["spec"]["jobReference"] == {
        "kind": "FlinkDeployment",
        "name": "orders",
    }
    assert data["spec"]["savepoint"]["disposeOnDelete"] is False


def test_dockerfile_templates_are_non_root_runtime_images():
    jvm = blocks(SKILL)["Dockerfile.jvm"]
    python = blocks(SKILL)["Dockerfile.pyflink"]
    assert "COPY __JOB_JAR__ /opt/flink/usrlib/job.jar" in jvm
    assert "COPY __PYTHON_PROJECT__/ /opt/flink/usrlib/python/" in python
    assert jvm.rstrip().endswith("USER flink")
    assert python.rstrip().endswith("USER flink")


def test_skill_pins_stable_docs_but_requires_runtime_discovery():
    text = SKILL.read_text(encoding="utf-8")
    assert "Operator 1.15" in text
    assert "always discover" in text
    assert "server-side dry-run" in text
    assert "Operator pod itself" in text
    assert "present only in the Session Cluster image" in text


def test_minikube_smoke_fixtures_are_valid_operator_resources():
    expected = {
        "minikube-application.yaml": "FlinkDeployment",
        "minikube-session-cluster.yaml": "FlinkDeployment",
        "minikube-session-job.yaml": "FlinkSessionJob",
        "minikube-snapshot.yaml": "FlinkStateSnapshot",
    }
    for filename, kind in expected.items():
        data = yaml.safe_load((FIXTURES / filename).read_text(encoding="utf-8"))
        assert data["apiVersion"] == "flink.apache.org/v1beta1"
        assert data["kind"] == kind
        assert data["metadata"]["namespace"] == "datus-flink-operator-test"
