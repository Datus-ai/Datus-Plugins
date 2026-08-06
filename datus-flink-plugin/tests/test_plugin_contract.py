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
SKILLS = PKG / "skills"
SKILL = SKILLS / "flink-k8s-operator"
LOCAL_DEV = SKILLS / "flink-local-dev"


def manifest() -> dict:
    return yaml.safe_load((PKG / "datus-plugin.yml").read_text(encoding="utf-8"))


def skill_frontmatter(skill: Path = SKILL) -> dict:
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None
    return yaml.safe_load(match.group(1))


def test_manifest_is_intentionally_skill_only():
    data = manifest()
    assert data == {
        "manifest_version": 1,
        "description": (
            "Validate Apache Flink jobs locally and build, deploy, and operate them "
            "through bundled runtime-specific skills."
        ),
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


def test_operator_skill_frontmatter():
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


def test_local_dev_skill_frontmatter():
    frontmatter = skill_frontmatter(LOCAL_DEV)
    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == "flink-local-dev"
    for phrase in (
        "in-process MiniCluster",
        "SQL Client",
        "before promoting it to production",
        "flink-k8s-operator",
        "never writes to a production sink",
    ):
        assert phrase in frontmatter["description"], phrase


def test_bundled_skills_are_exactly_the_two_documented_runtimes():
    assert sorted(path.name for path in SKILLS.iterdir()) == [
        "flink-k8s-operator",
        "flink-local-dev",
    ]


def test_every_skill_is_one_self_contained_file():
    """Datus discovers a skill's SKILL.md only — no assets/ or references/ directory.

    Everything a skill hands to a project (manifests, Dockerfiles, SQL overlays,
    the runner script) must therefore be inlined in that single file.
    """
    for skill in (SKILL, LOCAL_DEV):
        assert [path.name for path in skill.iterdir()] == ["SKILL.md"], sorted(
            path.name for path in skill.iterdir()
        )
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        # No links to sibling files that cannot ship with the skill.
        assert re.search(r"\]\((?!https?://|#)[^)]+\)", text) is None, skill


def test_local_dev_hands_production_deployment_to_the_operator_skill():
    skill = (LOCAL_DEV / "SKILL.md").read_text(encoding="utf-8")
    assert "flink-k8s-operator" in skill
    # The local skill validates; it never deploys or builds production images.
    for forbidden in ("datus k8s ", "docker build", "docker push"):
        assert forbidden not in skill, forbidden


def test_local_dev_never_recommends_a_remote_execution_target():
    text = (LOCAL_DEV / "SKILL.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        assert not re.search(
            r"^\s*(SET\s+'execution\.target'\s*=\s*'(?!local)|-Dexecution\.target=(?!local))",
            line,
        ), line


def test_workload_examples_never_invoke_kubectl():
    for path in SKILLS.rglob("*.md"):
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
