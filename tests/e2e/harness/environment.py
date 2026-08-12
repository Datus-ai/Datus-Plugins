"""Owned minikube fixtures. No workflow-provided shell is executed here."""

from __future__ import annotations

import json
import re
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import yaml

from .subprocesses import run_command


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


@dataclass
class EnvironmentContext:
    repo_root: Path
    suite_id: str
    run_id: str
    run_dir: Path
    workflow_dir: Path
    components: tuple[str, ...]
    lock: dict[str, Any]
    keep_suite: bool
    delete_namespace: bool = True
    delete_bucket_prefix: bool = True
    created_suite: bool = False
    namespace: str = field(init=False)
    profile: str = field(init=False)
    suite_dir: Path = field(init=False)
    kubeconfig: Path = field(init=False)
    logs: Path = field(init=False)
    port_forward: subprocess.Popen[str] | None = field(init=False, default=None)
    port_forward_stdout: TextIO | None = field(init=False, default=None)
    port_forward_stderr: TextIO | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if not SAFE_ID.fullmatch(self.suite_id) or not SAFE_ID.fullmatch(self.run_id):
            raise ValueError("suite_id and run_id must be safe lowercase DNS labels")
        self.profile = f"datus-e2e-{self.suite_id}"
        self.namespace = f"e2e-{self.run_id}"
        self.suite_dir = self.repo_root / ".datus-e2e" / "suites" / self.suite_id
        self.kubeconfig = self.suite_dir / "kubeconfig"
        self.logs = self.run_dir / "environment"

    @property
    def env(self) -> dict[str, str]:
        return {"KUBECONFIG": str(self.kubeconfig)}

    def command(self, argv: list[str | Path], name: str, *, timeout: int = 600, check: bool = True):
        return run_command(argv, cwd=self.repo_root, log_dir=self.logs, name=name, env=self.env, timeout=timeout, check=check)

    def kubectl(self, args: list[str], name: str, *, timeout: int = 600, check: bool = True):
        return self.command(["kubectl", "--kubeconfig", self.kubeconfig, *args], name, timeout=timeout, check=check)

    def up(self) -> dict[str, str]:
        if "minikube" not in self.components:
            return {"RUN_ID": self.run_id, "NAMESPACE": self.namespace}
        self.suite_dir.mkdir(parents=True, exist_ok=True)
        status = self.command(
            ["minikube", "-p", self.profile, "status", "--output=json"],
            "minikube-status",
            timeout=60,
            check=False,
        )
        running = False
        if status.returncode == 0:
            try:
                running = json.loads(status.stdout).get("Host") == "Running"
            except json.JSONDecodeError:
                running = False
        if not running:
            version = str(self.lock.get("kubernetes", "v1.35.0"))
            self.command(
                ["minikube", "start", "-p", self.profile, "--driver=docker", f"--kubernetes-version={version}"],
                "minikube-start",
                timeout=1800,
            )
            self.created_suite = True
        self.kubectl(["config", "use-context", self.profile], "select-owned-context")
        self.kubectl(["create", "namespace", self.namespace], "create-run-namespace", check=False)
        if "flink-operator" in self.components:
            self._up_flink_service_account()

        if "minio" in self.components:
            self._up_minio()
        if "flink-operator" in self.components:
            self._up_cert_manager()
            self._up_flink_operator()
        if "flink-paimon-runner" in self.components:
            self._build_runner_image()
        host_endpoint = self._forward_minio() if "minio" in self.components else ""
        return {
            "RUN_ID": self.run_id,
            "NAMESPACE": self.namespace,
            "KUBECONFIG": str(self.kubeconfig),
            "KUBE_CONTEXT": self.profile,
            "MINIO_ENDPOINT": "http://minio.datus-e2e-infra.svc.cluster.local:9000",
            "MINIO_HOST_ENDPOINT": host_endpoint,
            "MINIO_ACCESS_KEY": str(self.lock.get("minioAccessKey", "minioadmin")),
            "MINIO_SECRET_KEY": str(self.lock.get("minioSecretKey", "minioadmin")),
            "PAIMON_WAREHOUSE": f"s3://warehouse/{self.run_id}",
            "FLINK_RUNNER_IMAGE": f"datus-e2e/flink-paimon-runner:{self.suite_id}",
        }

    def _up_flink_service_account(self) -> None:
        self.kubectl(
            ["-n", self.namespace, "create", "serviceaccount", "flink"],
            "flink-service-account",
        )
        self.kubectl(
            [
                "-n",
                self.namespace,
                "create",
                "rolebinding",
                "flink-edit",
                "--clusterrole=edit",
                f"--serviceaccount={self.namespace}:flink",
            ],
            "flink-role-binding",
        )

    def _up_minio(self) -> None:
        manifest_path = self.repo_root / "tests/e2e/environments/minio.yaml"
        raw = manifest_path.read_text(encoding="utf-8")
        raw = raw.replace("{{MINIO_IMAGE}}", str(self.lock["minioImage"]))
        raw = raw.replace("{{MC_IMAGE}}", str(self.lock["mcImage"]))
        raw = raw.replace("{{MINIO_ACCESS_KEY}}", str(self.lock.get("minioAccessKey", "minioadmin")))
        raw = raw.replace("{{MINIO_SECRET_KEY}}", str(self.lock.get("minioSecretKey", "minioadmin")))
        rendered = self.suite_dir / "minio.yaml"
        rendered.write_text(raw, encoding="utf-8")
        self.kubectl(["apply", "-f", str(rendered)], "minio-apply")
        self.kubectl(
            ["-n", "datus-e2e-infra", "rollout", "status", "deployment/minio", "--timeout=300s"],
            "minio-ready",
            timeout=360,
        )
        self.kubectl(["-n", "datus-e2e-infra", "delete", "job", "create-warehouse", "--ignore-not-found=true"], "minio-bucket-reset")
        self.kubectl(["apply", "-f", str(rendered)], "minio-bucket-create")
        self.kubectl(
            ["-n", "datus-e2e-infra", "wait", "--for=condition=complete", "job/create-warehouse", "--timeout=180s"],
            "minio-bucket-ready",
            timeout=240,
        )

    def _up_flink_operator(self) -> None:
        version = str(self.lock["flinkOperatorChart"])
        repository = str(self.lock["flinkOperatorRepository"])
        self.command(
            [
                "helm", "upgrade", "--install", "flink-kubernetes-operator", "flink-kubernetes-operator",
                "--repo", repository, "--version", version, "--namespace", "flink-operator", "--create-namespace",
                "--wait", "--timeout", "10m",
            ],
            "flink-operator-install",
            timeout=900,
        )

    def _up_cert_manager(self) -> None:
        version = str(self.lock["certManagerChart"])
        repository = str(self.lock["certManagerRepository"])
        self.command(
            [
                "helm", "upgrade", "--install", "cert-manager", "cert-manager",
                "--repo", repository, "--version", version, "--namespace", "cert-manager", "--create-namespace",
                "--set", "crds.enabled=true", "--wait", "--timeout", "10m",
            ],
            "cert-manager-install",
            timeout=900,
        )

    def _build_runner_image(self) -> None:
        source = self.repo_root / "tests/e2e/environments/flink-paimon-runner"
        tag = f"datus-e2e/flink-paimon-runner:{self.suite_id}"
        self.command(
            [
                "minikube", "-p", self.profile, "image", "build", "-t", tag,
                "--build-opt", f"build-arg=FLINK_VERSION={self.lock['flink']}",
                "--build-opt", f"build-arg=PAIMON_FLINK_VERSION={'.'.join(str(self.lock['flink']).split('.')[:2])}",
                "--build-opt", f"build-arg=PAIMON_VERSION={self.lock['paimon']}",
                "--build-opt", f"build-arg=HADOOP_VERSION={self.lock['hadoop']}",
                str(source),
            ],
            "flink-runner-image",
            timeout=1800,
        )

    def _forward_minio(self) -> str:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.logs.mkdir(parents=True, exist_ok=True)
        self.port_forward_stdout = (self.logs / "minio-port-forward.stdout.log").open("w", encoding="utf-8")
        self.port_forward_stderr = (self.logs / "minio-port-forward.stderr.log").open("w", encoding="utf-8")
        env = os.environ.copy()
        env.update(self.env)
        self.port_forward = subprocess.Popen(
            [
                "kubectl", "--kubeconfig", str(self.kubeconfig), "-n", "datus-e2e-infra",
                "port-forward", "service/minio", f"{port}:9000", "--address=127.0.0.1",
            ],
            cwd=self.repo_root,
            env=env,
            text=True,
            stdout=self.port_forward_stdout,
            stderr=self.port_forward_stderr,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.port_forward.poll() is not None:
                raise RuntimeError("MinIO port-forward exited before becoming ready")
            with socket.socket() as client:
                client.settimeout(0.2)
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    return f"http://127.0.0.1:{port}"
            time.sleep(0.2)
        raise RuntimeError("MinIO port-forward did not become ready")

    def _cleanup_bucket(self) -> None:
        job_name = f"cleanup-{self.run_id}"[:63]
        manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name, "namespace": "datus-e2e-infra"},
            "spec": {
                "backoffLimit": 1,
                "template": {
                    "spec": {
                        "restartPolicy": "Never",
                        "containers": [
                            {
                                "name": "mc",
                                "image": str(self.lock["mcImage"]),
                                "command": ["/bin/sh", "-ec"],
                                "args": [
                                    "mc alias set local http://minio:9000 "
                                    f"{self.lock.get('minioAccessKey', 'minioadmin')} "
                                    f"{self.lock.get('minioSecretKey', 'minioadmin')}; "
                                    f"mc rm --recursive --force local/warehouse/{self.run_id}"
                                ],
                            }
                        ],
                    }
                },
            },
        }
        manifest_path = self.run_dir / "minio-cleanup-job.yml"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        self.kubectl(
            ["-n", "datus-e2e-infra", "delete", "job", job_name, "--ignore-not-found=true"],
            "minio-cleanup-reset",
            check=False,
        )
        applied = self.kubectl(["apply", "-f", str(manifest_path)], "minio-cleanup-apply", check=False)
        if applied.returncode == 0:
            self.kubectl(
                ["-n", "datus-e2e-infra", "wait", "--for=condition=complete", f"job/{job_name}", "--timeout=120s"],
                "minio-cleanup-wait",
                timeout=150,
                check=False,
            )
        self.kubectl(
            ["-n", "datus-e2e-infra", "delete", "job", job_name, "--ignore-not-found=true"],
            "minio-cleanup-delete",
            check=False,
        )

    def _stop_port_forward(self) -> None:
        if self.port_forward is not None and self.port_forward.poll() is None:
            self.port_forward.terminate()
            try:
                self.port_forward.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.port_forward.kill()
                self.port_forward.wait(timeout=5)
        for handle in (self.port_forward_stdout, self.port_forward_stderr):
            if handle is not None and not handle.closed:
                handle.close()

    def down(self) -> None:
        try:
            if "minikube" not in self.components:
                return
            if "minio" in self.components and self.delete_bucket_prefix:
                self._cleanup_bucket()
            if self.delete_namespace:
                self.kubectl(
                    ["delete", "namespace", self.namespace, "--ignore-not-found=true", "--wait=false"],
                    "delete-run-namespace",
                    check=False,
                )
            if not self.keep_suite:
                self.command(["minikube", "delete", "-p", self.profile], "minikube-delete", timeout=900, check=False)
        finally:
            self._stop_port_forward()


def load_environment_lock(workflow_dir: Path, environment: dict[str, Any]) -> dict[str, Any]:
    lock_path = environment.get("lock")
    if not lock_path:
        return {}
    value = yaml.safe_load((workflow_dir / lock_path).read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("environment lock must be a mapping")
    required_by_component = {
        "minio": {"minioImage", "mcImage"},
        "flink-operator": {
            "certManagerChart",
            "certManagerRepository",
            "flinkOperatorChart",
            "flinkOperatorRepository",
        },
        "flink-paimon-runner": {"flink", "hadoop", "paimon"},
    }
    for component in environment.get("components") or []:
        missing = required_by_component.get(component, set()) - set(value)
        if missing:
            raise ValueError(f"environment lock misses {sorted(missing)} for {component}")
    return value
