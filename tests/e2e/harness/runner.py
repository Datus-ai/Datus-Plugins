"""One complete workflow attempt, owned by pytest and invoked by the skills."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent import prepare_runtime, render_prompt, run_datus
from .artifacts import capture_generated, export_session, sha256, snapshot_text
from .environment import EnvironmentContext, load_environment_lock
from .oracles import run_oracles
from .process import check_efficiency, diagnose, load_payloads
from .schema import RunConfig, Workflow


def _safe_slug(value: str, limit: int = 32) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (value[:limit].rstrip("-") or "run")


def _copy_seed(workflow: Workflow, workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    if not workflow.seed:
        return
    source = workflow.path.parent / workflow.seed
    if source.is_dir():
        shutil.copytree(source, workspace, dirs_exist_ok=True)
    else:
        shutil.copy2(source, workspace / source.name)


def run_attempt(workflow: Workflow, run_config: RunConfig, *, repo_root: Path, attempt: int = 0) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    run_id = _safe_slug(f"{workflow.name}-{attempt}-{suffix}", 47)
    suite_id = _safe_slug(workflow.name, 32)
    run_dir = run_config.artifacts_root / workflow.name / run_id
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=False)
    _copy_seed(workflow, workspace)
    baseline = snapshot_text(workspace)
    lock = load_environment_lock(workflow.path.parent, workflow.environment)
    environment = EnvironmentContext(
        repo_root=repo_root,
        suite_id=suite_id,
        run_id=run_id,
        run_dir=run_dir,
        workflow_dir=workflow.path.parent,
        components=tuple(workflow.environment.get("components") or []),
        lock=lock,
        keep_suite=run_config.keep_suite,
        delete_namespace=bool(workflow.cleanup.get("deleteNamespace", True)),
        delete_bucket_prefix=bool(workflow.cleanup.get("deleteBucketPrefix", True)),
    )
    summary: dict[str, Any] = {
        "workflow": workflow.name,
        "run_id": run_id,
        "attempt": attempt,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "HARNESS_FAIL",
        "quality_passed": False,
        "run_dir": str(run_dir),
    }
    try:
        variables = environment.up()
        config_dir = workspace / "config"
        config_dir.mkdir(exist_ok=True)
        if environment.kubeconfig.exists():
            owned_kubeconfig = config_dir / "kubeconfig"
            flattened = environment.command(
                [
                    "kubectl",
                    "--kubeconfig",
                    environment.kubeconfig,
                    "config",
                    "view",
                    "--flatten",
                    "--minify",
                    "--raw",
                ],
                "flatten-owned-kubeconfig",
                timeout=60,
            )
            owned_kubeconfig.write_text(flattened.stdout, encoding="utf-8")
            if os.name != "nt":
                owned_kubeconfig.chmod(0o600)
            variables["KUBECONFIG"] = str(owned_kubeconfig)
        prompt = render_prompt(workflow, variables)
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        runtime = prepare_runtime(workflow, run_config, variables, run_dir, workspace, repo_root)
        result = run_datus(runtime, workflow, prompt, run_dir)
        session = export_session(runtime.home, run_dir / "session")
        generated = capture_generated(workspace, workflow.outputs, run_dir / "generated-files", baseline)
        payloads, jsonl_errors = load_payloads(run_dir / "stdout.jsonl")
        process = diagnose(payloads, session.get("usage"))
        process["jsonl_errors"] = jsonl_errors
        process_failures = check_efficiency(process, workflow.efficiency)
        (run_dir / "process.json").write_text(json.dumps(process, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        oracle_results = run_oracles(workflow, workspace=workspace, environment=environment, variables=variables, run_dir=run_dir)
        oracle_payload = {"passed": all(item.passed for item in oracle_results), "results": [item.as_dict() for item in oracle_results]}
        (run_dir / "oracle.json").write_text(json.dumps(oracle_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        correctness = result.returncode == 0 and oracle_payload["passed"] and not jsonl_errors
        summary.update(
            {
                "status": "PASS" if correctness else "PRODUCT_FAIL",
                "quality_passed": not process_failures,
                "agent_sha": runtime.sha,
                "datus_exit_code": result.returncode,
                "bundles": [{"path": str(path), "sha256": sha256(path)} for path in runtime.bundles],
                "generated": generated,
                "session": session,
                "process_failures": process_failures,
                "oracle_passed": oracle_payload["passed"],
            }
        )
    except Exception as exc:  # noqa: BLE001 - classify and persist harness failures
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            environment.down()
        except Exception as exc:  # noqa: BLE001
            summary["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            summary["status"] = "HARNESS_FAIL"
        if os.name != "nt":
            child_config = workspace / "conf/agent.yml"
            child_config_dir = child_config.parent
            if child_config_dir.exists():
                child_config_dir.chmod(0o700)
            if child_config.exists():
                child_config.chmod(0o600)
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return summary


def run_repeated(workflow: Workflow, run_config: RunConfig, *, repo_root: Path) -> list[dict[str, Any]]:
    return [run_attempt(workflow, run_config, repo_root=repo_root, attempt=index) for index in range(run_config.repeats)]
