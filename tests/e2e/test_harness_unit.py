from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.e2e.harness.agent import _write_configs, prepare_agent_source, resolve_agent_sha
from tests.e2e.harness.artifacts import capture_generated, export_session, redact, sha256, snapshot_text
from tests.e2e.harness.process import check_efficiency, diagnose, load_payloads
from tests.e2e.harness.environment import EnvironmentContext, load_environment_lock
from tests.e2e.harness.oracles import _files, _query_export_manifest, _superset_chart_datasource_id
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


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ({"datasource_id": 7}, 7),
        ({"datasource": {"id": 8}}, 8),
        ({"query_context": '{"datasource":{"id":9,"type":"table"}}'}, 9),
        ({"query_context": {"datasource": {"id": 10, "type": "table"}}}, 10),
        ({"query_context": "not-json"}, None),
    ],
)
def test_superset_chart_datasource_id_supports_api_projections(detail, expected):
    assert _superset_chart_datasource_id(detail) == expected


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
    assert process["effective_tokens"] == 42

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
                    "payload": {
                        "requests": 2,
                        "input_tokens": 10,
                        "output_tokens": 3,
                        "total_tokens": 13,
                        "cached_tokens": 7,
                    },
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
    assert process["cached_input_tokens"] == 7
    assert process["effective_tokens"] == 6
    assert check_efficiency(process, {"maxTokens": 6}) == []
    assert check_efficiency(process, {"maxTokens": 5}) == ["effective tokens: 6 exceeds 5"]


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
            "CREATE TABLE turn_usage (requests INTEGER, input_tokens INTEGER, output_tokens INTEGER, "
            "total_tokens INTEGER, input_tokens_details JSON)"
        )
        conn.executemany(
            "INSERT INTO turn_usage VALUES (?, ?, ?, ?, ?)",
            [(1, 10, 3, 13, '{"cached_tokens": 6}'), (1, 7, 2, 9, '{"cached_tokens": 4}')],
        )
    (session_dir / "latest.sysprompt.json").write_text(
        json.dumps({"token": "secret", "template": "use OPENAI_API_KEY"}), encoding="utf-8"
    )

    result = export_session(home, tmp_path / "export")

    assert result["usage"] == {
        "requests": 2,
        "input_tokens": 17,
        "output_tokens": 5,
        "total_tokens": 22,
        "cached_input_tokens": 10,
        "uncached_input_tokens": 7,
        "effective_tokens": 12,
    }
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


def test_files_oracle_rejects_forbidden_content(tmp_path: Path):
    manifest = tmp_path / "deploy/flink/run/flinkdeployment.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "job:\n  entryClass: org.apache.flink.table.runtime.application.SqlDriver\n",
        encoding="utf-8",
    )
    config = {
        "patterns": ["deploy/flink/*/*.yaml"],
        "notContent": {"deploy/flink/*/*.yaml": [r"(?m)^\s*jarURI\s*:"]},
    }

    assert _files(config, workspace=tmp_path).passed

    manifest.write_text(
        "job:\n  jarURI: local:///opt/flink/usrlib/sql-runner.jar\n",
        encoding="utf-8",
    )
    result = _files(config, workspace=tmp_path)

    assert not result.passed
    assert "contained forbidden" in (result.error or "")


def test_query_export_manifest_checks_count_language_hash_and_sources(tmp_path: Path):
    root = tmp_path / "reference_sql/grafana/prometheus-e2e-overview"
    source = root / "_source"
    source.mkdir(parents=True)
    query = "sum by (job) (up{job=~\"$job\"})\n"
    (root / "1-up-a.promql").write_text(query, encoding="utf-8")
    (source / "dashboard.json").write_text('{"title":"safe"}\n', encoding="utf-8")
    manifest = {
        "platform": "grafana",
        "summary": {"total": 1, "succeeded": 1, "failed": 0},
        "queries": [
            {
                "language": "promql",
                "status": "ok",
                "file": "1-up-a.promql",
                "sha256": sha256(root / "1-up-a.promql"),
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    config = {
        "manifest": "reference_sql/grafana/prometheus-e2e-overview/manifest.json",
        "platform": "grafana",
        "count": 1,
        "language": "promql",
        "suffix": ".promql",
        "forbidSuffix": ".sql",
        "requiredText": ["$job"],
    }

    assert _query_export_manifest(config, workspace=tmp_path).passed

    (source / "dashboard.json").write_text('{"password":"leak"}\n', encoding="utf-8")
    result = _query_export_manifest(config, workspace=tmp_path)
    assert not result.passed
    assert "secret fields" in (result.error or "")


@pytest.mark.parametrize(
    ("component", "required"),
    [
        ("superset-postgres", ["supersetImage", "postgresImage", "psycopg2Version"]),
        ("grafana-prometheus", ["grafanaImage", "prometheusImage", "nodeExporterImage"]),
    ],
)
def test_environment_lock_requires_dashboard_fixture_images(tmp_path: Path, component: str, required: list[str]):
    (tmp_path / "environment.lock.yml").write_text("kubernetes: v1.35.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match=component):
        load_environment_lock(
            tmp_path,
            {"components": [component], "lock": "environment.lock.yml"},
        )

    values = {"kubernetes": "v1.35.0", **{key: "pinned" for key in required}}
    (tmp_path / "environment.lock.yml").write_text(yaml.safe_dump(values), encoding="utf-8")
    assert load_environment_lock(
        tmp_path,
        {"components": [component], "lock": "environment.lock.yml"},
    ) == values


def test_wait_deployment_collects_failure_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    context = EnvironmentContext(
        repo_root=tmp_path,
        suite_id="suite",
        run_id="run",
        run_dir=tmp_path / "run",
        workflow_dir=tmp_path,
        components=("minikube",),
        lock={},
        keep_suite=False,
    )
    calls: list[tuple[list[str], str]] = []

    def fake_kubectl(args, name, **_kwargs):
        calls.append((args, name))
        if name == "prometheus-ready":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="rollout timed out")
        evidence = {
            "prometheus-failure-pods": "prometheus-abc 0/1 Pending",
            "prometheus-failure-describe": "FailedScheduling: insufficient memory",
            "prometheus-failure-events": "Warning FailedScheduling",
        }[name]
        return subprocess.CompletedProcess(args, 0, stdout=evidence, stderr="")

    monkeypatch.setattr(context, "kubectl", fake_kubectl)

    with pytest.raises(RuntimeError, match="insufficient memory"):
        context._wait_deployment("prometheus", timeout=15)

    assert [name for _, name in calls] == [
        "prometheus-ready",
        "prometheus-failure-pods",
        "prometheus-failure-describe",
        "prometheus-failure-events",
    ]
    assert calls[0][0][-1] == "--timeout=15s"


def test_redact_handles_nested_secret_values():
    assert redact({"password": "value", "nested": ["token=abc", "use MY_SECRET_TOKEN"]}) == {
        "password": "<redacted>",
        "nested": ["token=<redacted>", "use <redacted-env>"],
    }


def test_local_agent_source_is_exported_at_pinned_sha_without_git_metadata(tmp_path: Path):
    repo = tmp_path / "agent checkout"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "E2E Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "e2e@example.test"], cwd=repo, check=True)
    tracked = repo / "version.txt"
    tracked.write_text("pinned\n", encoding="utf-8")
    subprocess.run(["git", "add", "version.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "pinned"], cwd=repo, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    tracked.write_text("working tree change\n", encoding="utf-8")

    source = prepare_agent_source(
        str(repo), sha, cache_root=tmp_path / "cache", repo_root=tmp_path, log_dir=tmp_path / "logs"
    )

    assert (source / "version.txt").read_text(encoding="utf-8") == "pinned\n"
    assert (source / ".datus-source-sha").read_text(encoding="utf-8").strip() == sha
    assert not (source / ".git").exists()


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
    assert agent["config_mutable"] is False
    assert agent["plugins"]["sample"]["e2e"]["default"] is True
    assert agent["bash"]["sandbox"] == {"enabled": True, "mode": "strict", "deny_network": False}
    config_path.parent.chmod(0o700)
    config_path.chmod(0o600)
