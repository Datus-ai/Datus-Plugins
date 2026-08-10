from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from tests.e2e.harness.agent import _write_configs, agent_install_source, resolve_agent_sha
from tests.e2e.harness.artifacts import capture_generated, export_session, redact, sha256, snapshot_text
from tests.e2e.harness.process import check_efficiency, diagnose, load_payloads
from tests.e2e.harness.schema import ContractError, RunConfig, Workflow


def _minimal_workflow(tmp_path: Path) -> tuple[dict, Path]:
    (tmp_path / "prompt.md").write_text("do the deterministic task", encoding="utf-8")
    path = tmp_path / "workflow.yml"
    return (
        {
            "apiVersion": "datus.ai/v1alpha1",
            "kind": "PluginE2EWorkflow",
            "metadata": {"name": "sample", "description": "sample"},
            "spec": {
                "target": {"distribution": "sample-plugin", "path": "sample-plugin", "name": "sample"},
                "agent": {"prompt": "prompt.md", "timeoutSeconds": 30},
                "environment": {"components": []},
                "workspace": {"outputs": ["results/*.json"]},
                "oracles": [{"type": "files", "config": {"patterns": ["results/*.json"]}}],
                "cleanup": {"deleteNamespace": True, "deleteBucketPrefix": True},
            },
        },
        path,
    )


def test_workflow_rejects_unknown_fields(tmp_path: Path):
    raw, path = _minimal_workflow(tmp_path)
    raw["spec"]["surprise"] = True

    with pytest.raises(ContractError, match="unknown fields: surprise"):
        Workflow.parse(raw, path)


@pytest.mark.parametrize("pattern", ["../secret", "/tmp/result.json"])
def test_workflow_rejects_unsafe_output_patterns(tmp_path: Path, pattern: str):
    raw, path = _minimal_workflow(tmp_path)
    raw["spec"]["workspace"]["outputs"] = [pattern]

    with pytest.raises(ContractError, match="unsafe output pattern"):
        Workflow.parse(raw, path)


def test_workflow_rejects_non_boolean_cleanup(tmp_path: Path):
    raw, path = _minimal_workflow(tmp_path)
    raw["spec"]["cleanup"]["deleteNamespace"] = "yes"

    with pytest.raises(ContractError, match="must be a boolean"):
        Workflow.parse(raw, path)


def test_jsonl_process_diagnostics_and_efficiency(tmp_path: Path):
    stream = tmp_path / "stdout.jsonl"
    records = [
        {"type": "tool_call", "tool_name": "bash", "arguments": {"command": "datus k8s get pods"}},
        {"type": "tool_call", "tool": "bash", "input": {"cmd": "datus k8s get pods"}, "status": "failed"},
        {"type": "tool_call", "name": "write", "input": {"path": "result.json"}, "success": True},
    ]
    stream.write_text("\n".join(json.dumps(item) for item in records) + "\nnot-json\n", encoding="utf-8")

    payloads, errors = load_payloads(stream)
    process = diagnose(payloads, {"requests": 3, "input_tokens": 40, "output_tokens": 2, "total_tokens": 42})

    assert len(errors) == 1
    assert process["tool_sequence"] == ["bash", "bash", "write"]
    assert process["commands"] == ["datus k8s get pods", "datus k8s get pods"]
    assert process["duplicate_commands"] == [{"command": "datus k8s get pods", "count": 2}]
    assert process["unexpected_failures"] == [{"tool": "bash", "status": "failed"}]
    assert process["total_tokens"] == 42

    failures = check_efficiency(
        process,
        {
            "maxToolCalls": 2,
            "maxLlmTurns": 2,
            "maxTokens": 41,
            "maxUnexpectedFailures": 0,
            "forbiddenCommands": [r"\bkubectl\b"],
            "expectedCommands": [r"^datus k8s get"],
        },
    )
    assert len(failures) == 4


def test_process_understands_datus_message_payload_shape():
    payloads = [
        {
            "message_id": "one",
            "content": [
                {
                    "type": "call-tool",
                    "payload": {"callToolId": "c1", "toolName": "bash", "toolParams": {"command": "datus s3 stat x"}},
                },
                {
                    "type": "call-tool-result",
                    "payload": {"callToolId": "c1", "toolName": "bash", "result": {"success": False}},
                },
                {
                    "type": "usage",
                    "payload": {"requests": 2, "input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                },
            ],
        }
    ]

    process = diagnose(payloads)

    assert process["tool_sequence"] == ["bash"]
    assert process["commands"] == ["datus s3 stat x"]
    assert process["unexpected_failures"] == [{"tool": "bash", "status": "false"}]
    assert process["llm_turns"] == 2
    assert process["total_tokens"] == 13


def test_export_session_aggregates_usage_and_redacts(tmp_path: Path):
    home = tmp_path / "home"
    session_dir = home / "sessions" / "scope"
    session_dir.mkdir(parents=True)
    database = session_dir / "session.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE agent_messages (id INTEGER PRIMARY KEY, message_data TEXT)")
        conn.execute(
            "INSERT INTO agent_messages(message_data) VALUES (?)",
            (json.dumps({"role": "user", "api_key": "top-secret", "text": "Bearer abc.def"}),),
        )
        conn.execute(
            "CREATE TABLE turn_usage (requests INTEGER, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER)"
        )
        conn.executemany("INSERT INTO turn_usage VALUES (?, ?, ?, ?)", [(1, 10, 3, 13), (1, 7, 2, 9)])
    (session_dir / "latest.sysprompt.json").write_text(
        json.dumps({"token": "secret", "template": "use OPENAI_API_KEY"}), encoding="utf-8"
    )

    result = export_session(home, tmp_path / "export")

    assert result["usage"] == {"requests": 2, "input_tokens": 17, "output_tokens": 5, "total_tokens": 22}
    assert result["messages"] == 1
    message = json.loads((tmp_path / "export/session.jsonl").read_text(encoding="utf-8"))
    assert message == {"role": "user", "api_key": "<redacted>", "text": "Bearer <redacted>"}
    system_prompt = json.loads((tmp_path / "export/system-prompt.json").read_text(encoding="utf-8"))
    assert system_prompt == {"token": "<redacted>", "template": "use <redacted-env>"}
    assert (tmp_path / "export/session.db").is_file()


def test_capture_generated_hashes_and_patches_without_following_symlinks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    result_dir = workspace / "results"
    result_dir.mkdir(parents=True)
    report = result_dir / "report.md"
    report.write_text("before\n", encoding="utf-8")
    baseline = snapshot_text(workspace)
    report.write_text("after\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (result_dir / "outside.md").symlink_to(outside)

    captured = capture_generated(workspace, ("results/*.md",), tmp_path / "artifacts/generated", baseline)

    assert captured == [{"path": "results/report.md", "sha256": sha256(report), "bytes": 6}]
    patch = (tmp_path / "artifacts/generated.patch").read_text(encoding="utf-8")
    assert "-before" in patch
    assert "+after" in patch
    assert not (tmp_path / "artifacts/generated/results/outside.md").exists()


def test_redact_handles_nested_secret_values():
    assert redact({"password": "value", "nested": ["token=abc", "use MY_SECRET_TOKEN"]}) == {
        "password": "<redacted>",
        "nested": ["token=<redacted>", "use <redacted-env>"],
    }


def test_local_agent_source_is_pinned_to_sha(tmp_path: Path):
    repo = tmp_path / "agent checkout"
    repo.mkdir()
    sha = "a" * 40

    assert agent_install_source(str(repo), sha) == f"git+{repo.resolve().as_uri()}@{sha}"


def test_remote_annotated_tag_resolves_to_peeled_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    commit = "b" * 40
    tag = "c" * 40

    def fake_run(*_args, **_kwargs):
        class Result:
            stdout = f"{tag}\trefs/tags/v1\n{commit}\trefs/tags/v1^{{}}\n"

        return Result()

    monkeypatch.setattr("tests.e2e.harness.agent.run_command", fake_run)

    assert resolve_agent_sha("https://example.test/agent.git", "v1", repo_root=tmp_path, log_dir=tmp_path) == commit


def test_agent_config_is_visible_to_child_datus_commands(tmp_path: Path):
    raw, path = _minimal_workflow(tmp_path)
    workflow = Workflow.parse(raw, path)
    base_config = tmp_path / "base-agent.yml"
    base_config.write_text("agent:\n  providers: {}\n", encoding="utf-8")
    run_config = RunConfig(
        agent_repo="https://example.test/agent.git",
        agent_ref="a" * 40,
        base_config=base_config,
        plugin_root=tmp_path,
    )
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)

    config_path, project_path, home = _write_configs(
        workflow,
        run_config,
        {"RUN_ID": "sample-run"},
        run_dir,
        workspace,
    )

    assert config_path == workspace / "conf/agent.yml"
    assert project_path == workspace / ".datus/config.yml"
    assert home == run_dir / "datus-home"
    agent = yaml.safe_load(config_path.read_text(encoding="utf-8"))["agent"]
    assert agent["home"] == str(home)
    assert agent["plugins"]["sample"]["e2e"]["default"] is True
    assert agent["bash"]["sandbox"] == {"enabled": True, "mode": "strict", "deny_network": False}
    config_path.parent.chmod(0o700)
    config_path.chmod(0o600)
