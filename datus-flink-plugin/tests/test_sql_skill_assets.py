"""Validate the version-specific Flink SQL application templates."""

from __future__ import annotations

from pathlib import Path

import yaml
from skill_blocks import blocks, languages, render

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "datus_flink_plugin" / "skills" / "flink-sql" / "SKILL.md"

VALUES = {
    "__RUNNER_ENTRY_CLASS__": "com.example.SqlRunner",
    "__PARALLELISM__": "2",
    "__UPGRADE_MODE__": "stateless",
    "__FLINK_BASE_IMAGE__": "flink:2.0.0-scala_2.12-java17",
    "__SQL_FILE__": "sql/job.sql",
    "__CONNECTOR_JARS__": "connectors",
}


def rendered_yaml(name: str) -> dict:
    return yaml.safe_load(render(blocks(SKILL)[name], VALUES))


def test_skill_carries_both_job_variants_and_the_build_free_2x_image():
    assert set(blocks(SKILL)) == {
        "job-flink1.yaml",
        "job-flink2.yaml",
        "Dockerfile.flink2",
    }
    assert languages(SKILL) == {
        "job-flink1.yaml": "yaml",
        "job-flink2.yaml": "yaml",
        "Dockerfile.flink2": "dockerfile",
    }


def test_flink_1x_job_requires_a_runner_jar():
    job = rendered_yaml("job-flink1.yaml")["job"]
    assert job["jarURI"] == "local:///opt/flink/usrlib/sql-runner.jar"
    assert job["entryClass"] == "com.example.SqlRunner"
    assert job["args"] == ["--scriptUri", "file:///opt/flink/usrlib/job.sql"]


def test_flink_2x_job_uses_sqldriver_without_jar_uri():
    spec = rendered_yaml("job-flink2.yaml")["spec"]
    assert spec["mode"] == "standalone"
    job = spec["job"]
    assert "jarURI" not in job
    assert job["entryClass"] == "org.apache.flink.table.runtime.application.SqlDriver"
    assert job["args"] == ["--scriptUri", "file:///opt/flink/usrlib/job.sql"]


def test_flink_2x_dockerfile_has_no_application_build_stage():
    dockerfile = render(blocks(SKILL)["Dockerfile.flink2"], VALUES)
    assert "COPY sql/job.sql /opt/flink/usrlib/job.sql" in dockerfile
    assert "COPY connectors/ /opt/flink/lib/" in dockerfile
    assert "mvn" not in dockerfile
    assert "gradle" not in dockerfile
    assert dockerfile.rstrip().endswith("USER flink")


def test_skill_guards_the_sqldriver_runtime_contract():
    text = SKILL.read_text(encoding="utf-8")
    assert "Flink 2.0 introduced SQL Application Mode" in text
    assert "exactly one `flink-sql-gateway*.jar`" in text
    assert "ScriptRunner` as\nthe entry class" in text
    assert "It has no `main()`" in text
    assert "Do not copy Flink 2.x" in text
    assert "setting `pipeline.jars` or `pipeline.classpaths` to an empty string" in text
