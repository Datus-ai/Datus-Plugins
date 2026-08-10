"""Deterministic workflow oracles, independent from the tested plugin."""

from __future__ import annotations

import json
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from .environment import EnvironmentContext
from .schema import Workflow


@dataclass(frozen=True)
class OracleResult:
    type: str
    passed: bool
    evidence: dict[str, Any]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "passed": self.passed, "evidence": self.evidence, "error": self.error}


def _replace(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, variables) for item in value]
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace("{{" + key + "}}", replacement)
        return value
    return value


def _nested(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.strip(".").split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _files(config: dict[str, Any], *, workspace: Path, **_: Any) -> OracleResult:
    patterns = config.get("patterns") or []
    content = config.get("content") or {}
    found: dict[str, list[str]] = {}
    failures: list[str] = []
    for pattern in patterns:
        candidates = [path for path in sorted(workspace.glob(pattern)) if path.is_file()]
        symlinks = [path for path in candidates if path.is_symlink()]
        matches = [path for path in candidates if not path.is_symlink()]
        found[pattern] = [path.relative_to(workspace).as_posix() for path in matches]
        if symlinks:
            failures.append(f"symlinks are not accepted for {pattern!r}")
        if not matches:
            failures.append(f"no file matched {pattern!r}")
    for pattern, expressions in content.items():
        matches = [path for path in workspace.glob(pattern) if path.is_file() and not path.is_symlink()]
        for expression in expressions:
            if not any(re.search(expression, path.read_text(encoding="utf-8", errors="replace"), re.MULTILINE) for path in matches):
                failures.append(f"{pattern!r} did not contain /{expression}/")
    return OracleResult("files", not failures, {"matches": found, "failures": failures}, "; ".join(failures) or None)


def _kubernetes_resource(config: dict[str, Any], *, environment: EnvironmentContext, **_: Any) -> OracleResult:
    namespace = config.get("namespace", environment.namespace)
    resource = str(config["resource"])
    name = str(config["name"])
    result = environment.kubectl(["-n", namespace, "get", resource, name, "-o", "json"], f"oracle-{resource}-{name}", check=False)
    if result.returncode:
        return OracleResult("kubernetes_resource", False, {"resource": f"{resource}/{name}"}, result.stderr.strip())
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return OracleResult("kubernetes_resource", False, {}, f"invalid kubectl JSON: {exc}")
    expected = config.get("expected") or {}
    mismatches = {path: {"expected": wanted, "actual": _nested(value, path)} for path, wanted in expected.items() if _nested(value, path) != wanted}
    return OracleResult(
        "kubernetes_resource",
        not mismatches,
        {"resource": f"{resource}/{name}", "expected": expected, "mismatches": mismatches},
        "resource fields did not match" if mismatches else None,
    )


def _minio_object(config: dict[str, Any], *, environment: EnvironmentContext, workspace: Path, **_: Any) -> OracleResult:
    source = workspace / str(config["source"])
    if not source.is_file():
        return OracleResult("minio_object", False, {}, f"source fixture is missing: {source}")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    object_path = str(config["objectPath"])
    if not object_path.startswith("/data/warehouse/") or ".." in Path(object_path).parts:
        return OracleResult("minio_object", False, {}, "objectPath must stay under /data/warehouse")
    result = environment.kubectl(
        ["-n", "datus-e2e-infra", "exec", "deployment/minio", "--", "sha256sum", object_path],
        "oracle-minio-sha256",
        check=False,
    )
    actual = result.stdout.strip().split()[0] if result.returncode == 0 and result.stdout.strip() else None
    return OracleResult(
        "minio_object",
        actual == expected,
        {"objectPath": object_path, "expectedSha256": expected, "actualSha256": actual},
        None if actual == expected else (result.stderr.strip() or "object checksum did not match"),
    )


def _wait_flink_finished(environment: EnvironmentContext, deployment: str, timeout: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        result = environment.kubectl(
            ["-n", environment.namespace, "get", "flinkdeployment", deployment, "-o", "json"],
            "oracle-flink-status",
            check=False,
        )
        if result.returncode == 0:
            try:
                last = json.loads(result.stdout)
            except json.JSONDecodeError:
                last = {}
            state = _nested(last, "status.jobStatus.state")
            if state == "FINISHED":
                return True, last
            if state in {"FAILED", "FAILING"}:
                return False, last
        time.sleep(5)
    return False, last


def _flink_paimon(config: dict[str, Any], *, environment: EnvironmentContext, variables: dict[str, str], run_dir: Path, **_: Any) -> OracleResult:
    deployment = str(config["deployment"])
    finished, resource = _wait_flink_finished(environment, deployment, int(config.get("timeoutSeconds", 900)))
    state = _nested(resource, "status.jobStatus.state")
    if not finished:
        return OracleResult("flink_paimon", False, {"deployment": deployment, "state": state}, "Flink job did not finish")

    job_name = f"paimon-verify-{environment.run_id}"[:63]
    expected = {
        "schema": config.get("schema") or [],
        "primaryKey": config.get("primaryKey") or [],
        "count": int(config.get("count", 0)),
        "distinctId": int(config.get("distinctId", 0)),
        "minId": int(config.get("minId", 0)),
        "maxId": int(config.get("maxId", 0)),
        "source": config.get("source") or [],
    }
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": environment.namespace, "labels": {"datus-e2e-run": environment.run_id}},
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": {"job-name": job_name}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "verify",
                            "image": variables["FLINK_RUNNER_IMAGE"],
                            "imagePullPolicy": "Never",
                            "command": ["java"],
                            "args": [
                                "-cp", "/opt/flink/lib/*:/opt/flink/usrlib/sql-runner.jar",
                                "ai.datus.e2e.PaimonVerifier",
                                "--endpoint", variables["MINIO_ENDPOINT"],
                                "--warehouse", variables["PAIMON_WAREHOUSE"],
                                "--access-key", variables["MINIO_ACCESS_KEY"],
                                "--secret-key", variables["MINIO_SECRET_KEY"],
                                "--database", str(config.get("database", "e2e")),
                                "--table", str(config.get("table", "events")),
                                "--expected", json.dumps(expected, separators=(",", ":")),
                            ],
                        }
                    ],
                },
            },
        },
    }
    manifest_path = run_dir / "paimon-verifier-job.yml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    environment.kubectl(["delete", "job", job_name, "-n", environment.namespace, "--ignore-not-found=true"], "oracle-verifier-reset", check=False)
    environment.kubectl(["apply", "-f", str(manifest_path)], "oracle-verifier-apply")
    wait = environment.kubectl(
        ["-n", environment.namespace, "wait", "--for=condition=complete", f"job/{job_name}", "--timeout=300s"],
        "oracle-verifier-wait",
        timeout=360,
        check=False,
    )
    logs = environment.kubectl(["-n", environment.namespace, "logs", f"job/{job_name}"], "oracle-verifier-logs", check=False)
    marker = next((line.split("=", 1)[1] for line in logs.stdout.splitlines() if line.startswith("DATUS_E2E_ORACLE=")), None)
    evidence: dict[str, Any] = {"deployment": deployment, "state": state, "verifierExit": wait.returncode}
    if marker:
        try:
            evidence["verifier"] = json.loads(marker)
        except json.JSONDecodeError:
            evidence["verifierRaw"] = marker
    passed = wait.returncode == 0 and isinstance(evidence.get("verifier"), dict) and evidence["verifier"].get("passed") is True
    return OracleResult("flink_paimon", passed, evidence, None if passed else (logs.stderr or logs.stdout or "Paimon verifier failed").strip())


REGISTRY: dict[str, Callable[..., OracleResult]] = {
    "files": _files,
    "kubernetes_resource": _kubernetes_resource,
    "minio_object": _minio_object,
    "flink_paimon": _flink_paimon,
}


def run_oracles(
    workflow: Workflow,
    *,
    workspace: Path,
    environment: EnvironmentContext,
    variables: dict[str, str],
    run_dir: Path,
) -> list[OracleResult]:
    results: list[OracleResult] = []
    for spec in workflow.oracles:
        handler = REGISTRY.get(spec.type)
        if handler is None:
            results.append(OracleResult(spec.type, False, {}, f"oracle type is not implemented: {spec.type}"))
            continue
        config = _replace(spec.config, variables)
        try:
            results.append(handler(config, workspace=workspace, environment=environment, variables=variables, run_dir=run_dir))
        except Exception as exc:  # noqa: BLE001 - preserve every oracle failure as evidence
            results.append(OracleResult(spec.type, False, {}, f"{type(exc).__name__}: {exc}"))
    return results
