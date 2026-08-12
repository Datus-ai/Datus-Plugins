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
    port_forwards: list[subprocess.Popen[str]] = field(init=False, default_factory=list)
    port_forward_handles: list[TextIO] = field(init=False, default_factory=list)

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
            start_args = [
                "minikube", "start", "-p", self.profile, "--driver=docker", f"--kubernetes-version={version}"
            ]
            if self.lock.get("minikubeMemory"):
                start_args.append(f"--memory={self.lock['minikubeMemory']}")
            if self.lock.get("minikubeCpus"):
                start_args.append(f"--cpus={self.lock['minikubeCpus']}")
            self.command(
                start_args,
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
        if "superset-postgres" in self.components:
            self._up_superset_postgres()
        if "grafana-prometheus" in self.components:
            self._up_grafana_prometheus()

        variables = {
            "RUN_ID": self.run_id,
            "NAMESPACE": self.namespace,
            "KUBECONFIG": str(self.kubeconfig),
            "KUBE_CONTEXT": self.profile,
            "MINIO_ENDPOINT": "http://minio.datus-e2e-infra.svc.cluster.local:9000",
            "MINIO_HOST_ENDPOINT": "",
            "MINIO_ACCESS_KEY": str(self.lock.get("minioAccessKey", "minioadmin")),
            "MINIO_SECRET_KEY": str(self.lock.get("minioSecretKey", "minioadmin")),
            "PAIMON_WAREHOUSE": f"s3://warehouse/{self.run_id}",
            "FLINK_RUNNER_IMAGE": f"datus-e2e/flink-paimon-runner:{self.suite_id}",
        }
        if "minio" in self.components:
            variables["MINIO_HOST_ENDPOINT"] = self._forward_service(
                namespace="datus-e2e-infra", service="minio", remote_port=9000, name="minio"
            )
        if "superset-postgres" in self.components:
            endpoint = self._forward_service(
                namespace=self.namespace, service="superset", remote_port=8088, name="superset"
            )
            self._register_superset_database(endpoint)
            variables.update(
                {
                    "SUPERSET_HOST_ENDPOINT": endpoint,
                    "SUPERSET_USERNAME": str(self.lock.get("supersetUsername", "admin")),
                    "SUPERSET_PASSWORD": str(self.lock.get("supersetPassword", "admin")),
                    "POSTGRES_DATABASE": str(self.lock.get("postgresDatabase", "superset_examples")),
                    "POSTGRES_USERNAME": str(self.lock.get("postgresUsername", "superset")),
                    "POSTGRES_PASSWORD": str(self.lock.get("postgresPassword", "superset")),
                }
            )
        if "grafana-prometheus" in self.components:
            variables.update(
                {
                    "GRAFANA_HOST_ENDPOINT": self._forward_service(
                        namespace=self.namespace, service="grafana", remote_port=3000, name="grafana"
                    ),
                    "PROMETHEUS_HOST_ENDPOINT": self._forward_service(
                        namespace=self.namespace, service="prometheus", remote_port=9090, name="prometheus"
                    ),
                    "GRAFANA_USERNAME": str(self.lock.get("grafanaUsername", "admin")),
                    "GRAFANA_PASSWORD": str(self.lock.get("grafanaPassword", "admin")),
                }
            )
            self._wait_prometheus_targets(variables["PROMETHEUS_HOST_ENDPOINT"])
        return variables

    def _register_superset_database(self, endpoint: str) -> None:
        from http.cookiejar import CookieJar
        from urllib.error import HTTPError
        from urllib.request import HTTPCookieProcessor, Request, build_opener

        opener = build_opener(HTTPCookieProcessor(CookieJar()))

        def request(path: str, *, method: str = "GET", body: Any = None, headers: dict[str, str] | None = None) -> Any:
            data = json.dumps(body).encode() if body is not None else None
            request_headers = {"Accept": "application/json", **(headers or {})}
            if data is not None:
                request_headers["Content-Type"] = "application/json"
            try:
                with opener.open(  # noqa: S310 - fixed loopback endpoint owned by this fixture
                    Request(endpoint + path, data=data, headers=request_headers, method=method), timeout=30
                ) as response:
                    return json.load(response)
            except HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:2000]
                raise RuntimeError(f"Superset fixture HTTP {exc.code} for {method} {path}: {detail}") from exc

        login = request(
            "/api/v1/security/login",
            method="POST",
            body={
                "username": str(self.lock.get("supersetUsername", "admin")),
                "password": str(self.lock.get("supersetPassword", "admin")),
                "provider": "db",
                "refresh": True,
            },
        )
        token = login.get("access_token")
        if not token:
            raise RuntimeError("Superset fixture login did not return an access token")
        auth = {"Authorization": f"Bearer {token}"}
        csrf = request("/api/v1/security/csrf_token/", headers=auth).get("result")
        request(
            "/api/v1/database/",
            method="POST",
            headers={**auth, "X-CSRFToken": str(csrf or ""), "Referer": endpoint + "/"},
            body={
                "database_name": "E2E PostgreSQL",
                "sqlalchemy_uri": (
                    f"postgresql+psycopg2://{self.lock.get('postgresUsername', 'superset')}:"
                    f"{self.lock.get('postgresPassword', 'superset')}@postgres:5432/"
                    f"{self.lock.get('postgresDatabase', 'superset_examples')}"
                ),
                "expose_in_sqllab": True,
                "allow_ctas": True,
                "allow_cvas": True,
            },
        )

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

    def _render_environment_manifest(self, source_name: str, replacements: dict[str, str]) -> Path:
        source = self.repo_root / "tests/e2e/environments" / source_name
        raw = source.read_text(encoding="utf-8")
        for key, value in replacements.items():
            raw = raw.replace("{{" + key + "}}", value)
        unresolved = sorted(set(re.findall(r"{{([A-Z0-9_]+)}}", raw)))
        if unresolved:
            raise ValueError(f"environment manifest {source_name} has unresolved values: {unresolved}")
        rendered = self.run_dir / f"rendered-{source_name}"
        rendered.write_text(raw, encoding="utf-8")
        return rendered

    def _up_superset_postgres(self) -> None:
        image = f"datus-e2e/superset:{self.suite_id}"
        source = self.repo_root / "tests/e2e/environments/superset"
        self.command(
            [
                "minikube", "-p", self.profile, "image", "build", "-t", image,
                "--build-opt", f"build-arg=SUPERSET_IMAGE={self.lock['supersetImage']}",
                "--build-opt", f"build-arg=PSYCOPG2_VERSION={self.lock['psycopg2Version']}",
                str(source),
            ],
            "superset-image",
            timeout=1800,
        )
        manifest = self._render_environment_manifest(
            "superset-postgres.yaml",
            {
                "NAMESPACE": self.namespace,
                "POSTGRES_IMAGE": str(self.lock["postgresImage"]),
                "SUPERSET_IMAGE": image,
                "POSTGRES_USERNAME": str(self.lock.get("postgresUsername", "superset")),
                "POSTGRES_PASSWORD": str(self.lock.get("postgresPassword", "superset")),
                "POSTGRES_DATABASE": str(self.lock.get("postgresDatabase", "superset_examples")),
                "SUPERSET_USERNAME": str(self.lock.get("supersetUsername", "admin")),
                "SUPERSET_PASSWORD": str(self.lock.get("supersetPassword", "admin")),
                "SUPERSET_SECRET_KEY": str(self.lock.get("supersetSecretKey", "datus-e2e-only-secret-key")),
            },
        )
        self.kubectl(["apply", "-f", str(manifest)], "superset-postgres-apply")
        self.kubectl(
            ["-n", self.namespace, "rollout", "status", "deployment/postgres", "--timeout=300s"],
            "postgres-ready",
            timeout=360,
        )
        self._wait_job("postgres-seed", timeout=300)
        self._wait_job("superset-init", timeout=600)
        self.kubectl(
            ["-n", self.namespace, "scale", "deployment/superset", "--replicas=1"],
            "superset-scale",
        )
        self.kubectl(
            ["-n", self.namespace, "rollout", "status", "deployment/superset", "--timeout=600s"],
            "superset-ready",
            timeout=660,
        )

    def _wait_job(self, job: str, *, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.kubectl(
                ["-n", self.namespace, "get", "job", job, "-o", "json"],
                f"{job}-status",
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                try:
                    status = (json.loads(result.stdout) or {}).get("status") or {}
                except json.JSONDecodeError:
                    status = {}
                if int(status.get("succeeded") or 0) >= 1:
                    return
                terminal_failure = any(
                    item.get("type") == "Failed" and item.get("status") == "True"
                    for item in status.get("conditions") or []
                    if isinstance(item, dict)
                )
                if terminal_failure:
                    pods = self.kubectl(
                        ["-n", self.namespace, "get", "pods", "-l", f"job-name={job}", "-o", "json"],
                        f"{job}-failure-pods",
                        timeout=30,
                        check=False,
                    )
                    pod_items: list[dict[str, Any]] = []
                    try:
                        pod_items = (json.loads(pods.stdout) or {}).get("items") or []
                    except json.JSONDecodeError:
                        pass
                    evidence: list[str] = []
                    for index, pod in enumerate(pod_items):
                        name = str((pod.get("metadata") or {}).get("name") or "")
                        statuses = (pod.get("status") or {}).get("containerStatuses") or []
                        evidence.append(json.dumps({"pod": name, "containerStatuses": statuses}, default=str))
                        if name:
                            logs = self.kubectl(
                                ["-n", self.namespace, "logs", name, "--all-containers=true", "--tail=-1"],
                                f"{job}-failure-logs-{index}",
                                timeout=30,
                                check=False,
                            )
                            evidence.append((logs.stdout or logs.stderr).strip())
                    detail = "\n".join(evidence).strip()[-8000:] or "no failed Pod evidence was available"
                    raise RuntimeError(f"job/{job} failed: {detail}")
            time.sleep(3)
        raise RuntimeError(f"job/{job} did not complete within {timeout}s")

    def _up_grafana_prometheus(self) -> None:
        manifest = self._render_environment_manifest(
            "grafana-prometheus.yaml",
            {
                "NAMESPACE": self.namespace,
                "GRAFANA_IMAGE": str(self.lock["grafanaImage"]),
                "PROMETHEUS_IMAGE": str(self.lock["prometheusImage"]),
                "NODE_EXPORTER_IMAGE": str(self.lock["nodeExporterImage"]),
                "GRAFANA_USERNAME": str(self.lock.get("grafanaUsername", "admin")),
                "GRAFANA_PASSWORD": str(self.lock.get("grafanaPassword", "admin")),
            },
        )
        self.kubectl(["apply", "-f", str(manifest)], "grafana-prometheus-apply")
        ready_timeout = int(self.lock.get("deploymentReadyTimeoutSeconds", 300))
        for deployment in ("prometheus", "node-exporter", "grafana"):
            self._wait_deployment(deployment, timeout=ready_timeout)

    def _wait_deployment(self, deployment: str, *, timeout: int) -> None:
        result = self.kubectl(
            [
                "-n", self.namespace, "rollout", "status", f"deployment/{deployment}",
                f"--timeout={timeout}s",
            ],
            f"{deployment}-ready",
            timeout=timeout + 60,
            check=False,
        )
        if result.returncode == 0:
            return
        pods = self.kubectl(
            ["-n", self.namespace, "get", "pods", "-l", f"app={deployment}", "-o", "wide"],
            f"{deployment}-failure-pods",
            timeout=30,
            check=False,
        )
        describe = self.kubectl(
            ["-n", self.namespace, "describe", f"deployment/{deployment}"],
            f"{deployment}-failure-describe",
            timeout=30,
            check=False,
        )
        events = self.kubectl(
            ["-n", self.namespace, "get", "events", "--sort-by=.metadata.creationTimestamp"],
            f"{deployment}-failure-events",
            timeout=30,
            check=False,
        )
        detail = "\n".join(
            part.strip()
            for part in (result.stderr or result.stdout, pods.stdout, describe.stdout, events.stdout)
            if part.strip()
        )[-12000:]
        raise RuntimeError(f"deployment/{deployment} did not become ready:\n{detail}")

    def _wait_prometheus_targets(self, endpoint: str) -> None:
        from urllib.parse import urlencode
        from urllib.request import urlopen

        deadline = time.monotonic() + int(self.lock.get("prometheusReadyTimeoutSeconds", 180))
        ready_since: float | None = None
        while time.monotonic() < deadline:
            try:
                url = endpoint + "/api/v1/query?" + urlencode({"query": "up"})
                with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed loopback endpoint
                    payload = json.load(response)
                values = {
                    item.get("metric", {}).get("job"): item.get("value", [None, None])[1]
                    for item in payload.get("data", {}).get("result", [])
                }
                if values.get("prometheus") == "1" and values.get("node-exporter") == "1":
                    if ready_since is None:
                        ready_since = time.monotonic()
                    if time.monotonic() - ready_since >= int(self.lock.get("prometheusPreheatSeconds", 70)):
                        return
                else:
                    ready_since = None
            except (OSError, ValueError, json.JSONDecodeError):
                ready_since = None
            time.sleep(2)
        raise RuntimeError("Prometheus targets did not remain ready for the required preheat period")

    def _forward_service(self, *, namespace: str, service: str, remote_port: int, name: str) -> str:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.logs.mkdir(parents=True, exist_ok=True)
        stdout = (self.logs / f"{name}-port-forward.stdout.log").open("w", encoding="utf-8")
        stderr = (self.logs / f"{name}-port-forward.stderr.log").open("w", encoding="utf-8")
        env = os.environ.copy()
        env.update(self.env)
        process = subprocess.Popen(
            [
                "kubectl", "--kubeconfig", str(self.kubeconfig), "-n", namespace,
                "port-forward", f"service/{service}", f"{port}:{remote_port}", "--address=127.0.0.1",
            ],
            cwd=self.repo_root,
            env=env,
            text=True,
            stdout=stdout,
            stderr=stderr,
        )
        self.port_forwards.append(process)
        self.port_forward_handles.extend((stdout, stderr))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{name} port-forward exited before becoming ready")
            with socket.socket() as client:
                client.settimeout(0.2)
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    return f"http://127.0.0.1:{port}"
            time.sleep(0.2)
        raise RuntimeError(f"{name} port-forward did not become ready")

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
        for process in reversed(self.port_forwards):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for handle in self.port_forward_handles:
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
        "superset-postgres": {"supersetImage", "postgresImage", "psycopg2Version"},
        "grafana-prometheus": {"grafanaImage", "prometheusImage", "nodeExporterImage"},
    }
    for component in environment.get("components") or []:
        missing = required_by_component.get(component, set()) - set(value)
        if missing:
            raise ValueError(f"environment lock misses {sorted(missing)} for {component}")
    return value
