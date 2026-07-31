"""Contract tests for the skill-only Datus Flink plugin."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

PLUGIN_NAME = "flink"
ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "datus_flink_plugin"
SKILL = PKG / "skills" / "flink-k8s-operator"


def manifest() -> dict:
    return yaml.safe_load((PKG / "datus-plugin.yml").read_text(encoding="utf-8"))


def skill_frontmatter() -> dict:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None
    return yaml.safe_load(match.group(1))


def test_manifest_is_intentionally_skill_only():
    data = manifest()
    assert data == {
        "manifest_version": 1,
        "description": "Build and operate Apache Flink jobs through bundled runtime-specific skills.",
        "skills": "skills",
    }
    assert (PKG / data["skills"]).is_dir()
    for forbidden in (
        "cli",
        "commands",
        "permissions",
        "config_schema",
        "system_prompt",
        "tool_transformers",
    ):
        assert forbidden not in data


def test_entry_point_is_one_bare_package_mapping_with_no_runtime_dependencies():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["entry-points"]["datus.plugins"] == {
        PLUGIN_NAME: "datus_flink_plugin"
    }
    assert data["project"]["dependencies"] == []
    assert ":" not in data["project"]["entry-points"]["datus.plugins"][PLUGIN_NAME]


def test_skill_frontmatter_and_progressive_resources():
    assert skill_frontmatter() == {
        "name": "flink-k8s-operator",
        "description": (
            "Build, deploy, operate, upgrade, snapshot, and troubleshoot Apache Flink jobs "
            "running on the Flink Kubernetes Operator. Use for FlinkDeployment application "
            "clusters, session clusters, FlinkSessionJob, FlinkStateSnapshot, Java/Scala Maven "
            "or Gradle projects, PyFlink projects, minikube image loading, remote registry "
            "publishing, job status, logs, events, suspend/resume, restart, stateful upgrades, "
            "recovery, or deletion. Delegate every Kubernetes workload operation to `datus k8s`."
        ),
    }
    for relative in (
        "references/operator-crds.md",
        "references/build-and-images.md",
        "assets/flinkdeployment-application.yaml",
        "assets/flinkdeployment-session.yaml",
        "assets/flinksessionjob.yaml",
        "assets/flinkstatesnapshot.yaml",
        "assets/Dockerfile.jvm",
        "assets/Dockerfile.pyflink",
    ):
        assert (SKILL / relative).is_file(), relative


def test_workload_examples_never_invoke_kubectl():
    for path in SKILL.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"(?m)^\s*kubectl(?:\s|$)", text) is None, path


def test_package_never_imports_datus():
    package_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PKG.rglob("*.py")
    )
    assert re.search(r"(?m)^\s*(?:from|import)\s+datus\b", package_text) is None

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import datus_flink_plugin; "
            "assert not any(m == 'datus' or m.startswith('datus.') for m in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
