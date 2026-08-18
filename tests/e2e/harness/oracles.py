"""Deterministic workflow oracles, independent from the tested plugin."""

from __future__ import annotations

import json
import hashlib
import re
import time
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
    not_content = config.get("notContent") or {}
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
    for pattern, expressions in not_content.items():
        matches = [path for path in workspace.glob(pattern) if path.is_file() and not path.is_symlink()]
        for expression in expressions:
            offending = [
                path.relative_to(workspace).as_posix()
                for path in matches
                if re.search(expression, path.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
            ]
            if offending:
                failures.append(f"{pattern!r} contained forbidden /{expression}/ in {offending}")
    return OracleResult("files", not failures, {"matches": found, "failures": failures}, "; ".join(failures) or None)


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    headers: dict[str, str] | None = None,
    basic_auth: tuple[str, str] | None = None,
) -> Any:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if basic_auth:
        encoded = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
        request_headers["Authorization"] = f"Basic {encoded}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - endpoints come from owned fixtures
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} for {method} {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"cannot reach {url}: {exc.reason}") from exc


def _superset_session(endpoint: str, username: str, password: str) -> dict[str, str]:
    login = _http_json(
        endpoint + "/api/v1/security/login",
        method="POST",
        body={"username": username, "password": password, "provider": "db", "refresh": True},
    )
    token = login.get("access_token") if isinstance(login, dict) else None
    if not token:
        raise RuntimeError("Superset login did not return an access token")
    return {"Authorization": f"Bearer {token}"}


def _superset_chart_datasource_id(detail: dict[str, Any]) -> Any:
    """Read the dataset ID across Superset chart response projections."""
    datasource_id = detail.get("datasource_id")
    if datasource_id is None and isinstance(detail.get("datasource"), dict):
        datasource_id = detail["datasource"].get("id")
    query_context = detail.get("query_context")
    if datasource_id is None and isinstance(query_context, str):
        try:
            query_context = json.loads(query_context)
        except json.JSONDecodeError:
            query_context = None
    if datasource_id is None and isinstance(query_context, dict):
        datasource = query_context.get("datasource")
        if isinstance(datasource, dict):
            datasource_id = datasource.get("id")
    return datasource_id


def _superset_dashboard(
    config: dict[str, Any], *, variables: dict[str, str], **_: Any
) -> OracleResult:
    endpoint = variables["SUPERSET_HOST_ENDPOINT"]
    headers = _superset_session(endpoint, variables["SUPERSET_USERNAME"], variables["SUPERSET_PASSWORD"])
    configured_id = config.get("dashboardId")
    if configured_id is None:
        query = json.dumps(
            {
                "filters": [{"col": "slug", "opr": "eq", "value": config.get("slug")}],
                "page": 0,
                "page_size": 10,
            },
            separators=(",", ":"),
        )
        listed = _http_json(endpoint + "/api/v1/dashboard/?" + urlencode({"q": query}), headers=headers)
        matches = listed.get("result", []) if isinstance(listed, dict) else []
        if not isinstance(matches, list) or len(matches) != 1:
            return OracleResult(
                "superset_dashboard", False, {"matches": matches},
                f"expected exactly one dashboard with slug {config.get('slug')!r}",
            )
        configured_id = matches[0].get("id")
    dashboard_id = str(configured_id)
    dashboard_payload = _http_json(endpoint + f"/api/v1/dashboard/{dashboard_id}", headers=headers)
    dashboard = dashboard_payload.get("result", dashboard_payload)
    charts_payload = _http_json(endpoint + f"/api/v1/dashboard/{dashboard_id}/charts", headers=headers)
    charts = charts_payload.get("result", charts_payload)
    if not isinstance(dashboard, dict) or not isinstance(charts, list):
        return OracleResult("superset_dashboard", False, {}, "Superset returned an invalid dashboard document")
    failures: list[str] = []
    expected_title = config.get("title")
    expected_slug = config.get("slug")
    if dashboard.get("dashboard_title") != expected_title:
        failures.append(f"dashboard title was {dashboard.get('dashboard_title')!r}")
    if dashboard.get("slug") != expected_slug:
        failures.append(f"dashboard slug was {dashboard.get('slug')!r}")
    expected_charts = list(config.get("chartTitles") or [])
    chart_titles = [item.get("slice_name") for item in charts if isinstance(item, dict)]
    if sorted(chart_titles) != sorted(expected_charts):
        failures.append(f"chart titles were {chart_titles!r}")
    position = dashboard.get("position_json") or "{}"
    try:
        layout = json.loads(position) if isinstance(position, str) else position
    except json.JSONDecodeError:
        layout = {}
        failures.append("position_json was invalid JSON")
    layout_text = json.dumps(layout, sort_keys=True)
    chart_ids = [item.get("id") or item.get("slice_id") for item in charts if isinstance(item, dict)]
    missing_layout = [chart_id for chart_id in chart_ids if str(chart_id) not in layout_text]
    if missing_layout:
        failures.append(f"layout omitted chart ids {missing_layout}")

    expected_table = str(config.get("table", ""))
    chart_details: list[dict[str, Any]] = []
    dataset_ids: set[str] = set()
    query_rows: dict[str, list[dict[str, Any]]] = {}
    for chart_id in chart_ids:
        payload = _http_json(endpoint + f"/api/v1/chart/{chart_id}", headers=headers)
        detail = payload.get("result", payload)
        if isinstance(detail, dict):
            chart_details.append(detail)
            datasource_id = _superset_chart_datasource_id(detail)
            if datasource_id is not None:
                dataset_ids.add(str(datasource_id))
            else:
                failures.append(f"chart {chart_id} has no dataset id")
            query_context = detail.get("query_context")
            if isinstance(query_context, str):
                try:
                    query_context = json.loads(query_context)
                except json.JSONDecodeError:
                    query_context = None
            if not isinstance(query_context, dict):
                failures.append(f"chart {chart_id} has no query_context")
                continue
            response = _http_json(endpoint + f"/api/v1/chart/{chart_id}/data/", headers=headers)
            result_payloads = response.get("result") if isinstance(response, dict) else None
            if not isinstance(result_payloads, list) or not result_payloads:
                failures.append(f"chart {chart_id} query returned no result")
            else:
                rows = result_payloads[0].get("data") if isinstance(result_payloads[0], dict) else None
                title = str(detail.get("slice_name") or chart_id)
                query_rows[title] = rows if isinstance(rows, list) else []
                if not query_rows[title]:
                    failures.append(f"chart {chart_id} query returned no data rows")

    datasets: list[dict[str, Any]] = []
    for dataset_id in sorted(dataset_ids):
        payload = _http_json(endpoint + f"/api/v1/dataset/{dataset_id}", headers=headers)
        dataset = payload.get("result", payload)
        if isinstance(dataset, dict):
            datasets.append(dataset)
    if len(datasets) != 1:
        failures.append(f"charts referenced {len(datasets)} datasets")
    elif datasets[0].get("table_name") != expected_table or datasets[0].get("schema") != config.get("schema"):
        failures.append(
            f"dataset mapped to {datasets[0].get('schema')}.{datasets[0].get('table_name')}"
        )
    for title, expected_rows in (config.get("expectedRows") or {}).items():
        actual_rows = query_rows.get(title) or []
        for expected_row in expected_rows:
            if not any(
                all(str(actual.get(key)) == str(value) for key, value in expected_row.items())
                for actual in actual_rows
                if isinstance(actual, dict)
            ):
                failures.append(f"chart {title!r} omitted row {expected_row!r}; rows={actual_rows!r}")

    evidence = {
        "dashboardId": dashboard_id,
        "title": dashboard.get("dashboard_title"),
        "slug": dashboard.get("slug"),
        "chartIds": chart_ids,
        "chartTitles": chart_titles,
        "datasetIds": sorted(dataset_ids),
        "queriedCharts": len(chart_details),
        "queryRows": query_rows,
        "failures": failures,
    }
    return OracleResult("superset_dashboard", not failures, evidence, "; ".join(failures) or None)


def _postgres_query(
    config: dict[str, Any], *, environment: EnvironmentContext, variables: dict[str, str], **_: Any
) -> OracleResult:
    failures: list[str] = []
    observed: list[dict[str, Any]] = []
    for index, check in enumerate(config.get("queries") or []):
        sql = str(check["sql"])
        result = environment.kubectl(
            [
                "-n", environment.namespace, "exec", "deployment/postgres", "--",
                "psql", "-X", "-A", "-t", "-F", "\t",
                "-U", variables["POSTGRES_USERNAME"], "-d", variables["POSTGRES_DATABASE"],
                "-c", sql,
            ],
            f"oracle-postgres-{index}",
            check=False,
        )
        rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]
        expected = [[str(cell) for cell in row] for row in check.get("expectedRows") or []]
        observed.append({"sql": sql, "rows": rows, "expectedRows": expected, "exitCode": result.returncode})
        if result.returncode != 0:
            failures.append(f"query {index} failed: {result.stderr.strip()}")
        elif rows != expected:
            failures.append(f"query {index} rows were {rows!r}")
    return OracleResult("postgres_query", not failures, {"queries": observed, "failures": failures}, "; ".join(failures) or None)


def _grafana_dashboard(
    config: dict[str, Any], *, variables: dict[str, str], **_: Any
) -> OracleResult:
    endpoint = variables["GRAFANA_HOST_ENDPOINT"]
    auth = (variables["GRAFANA_USERNAME"], variables["GRAFANA_PASSWORD"])
    uid = str(config["uid"])
    namespace = str(config.get("namespace", "default"))
    payload = _http_json(
        endpoint + f"/apis/dashboard.grafana.app/v1beta1/namespaces/{namespace}/dashboards/{uid}",
        basic_auth=auth,
    )
    dashboard = payload.get("spec", {}) if isinstance(payload, dict) else {}
    datasource = _http_json(endpoint + "/api/datasources/uid/prometheus-e2e", basic_auth=auth)
    failures: list[str] = []
    if dashboard.get("title") != config.get("title"):
        failures.append(f"dashboard title was {dashboard.get('title')!r}")
    if datasource.get("uid") != "prometheus-e2e" or datasource.get("type") != "prometheus":
        failures.append("Prometheus datasource identity did not match")
    if datasource.get("url") != "http://prometheus:9090":
        failures.append(f"Prometheus datasource URL was {datasource.get('url')!r}")
    variables_list = ((dashboard.get("templating") or {}).get("list") or [])
    if not any(item.get("name") == "job" and item.get("type") == "query" for item in variables_list if isinstance(item, dict)):
        failures.append("$job query variable was missing")
    panels = [item for item in dashboard.get("panels", []) if isinstance(item, dict)]
    actual = {
        item.get("title"): [target.get("expr") for target in item.get("targets", []) if isinstance(target, dict)]
        for item in panels
    }
    expected = config.get("panels") or {}
    if actual != expected:
        failures.append(f"panel queries were {actual!r}")
    evidence = {
        "uid": uid,
        "title": dashboard.get("title"),
        "datasource": {key: datasource.get(key) for key in ("name", "uid", "type", "url")},
        "panels": actual,
        "failures": failures,
    }
    return OracleResult("grafana_dashboard", not failures, evidence, "; ".join(failures) or None)


def _prometheus_query(config: dict[str, Any], *, variables: dict[str, str], **_: Any) -> OracleResult:
    endpoint = variables["PROMETHEUS_HOST_ENDPOINT"]
    failures: list[str] = []
    evidence: list[dict[str, Any]] = []
    for raw in config.get("queries") or []:
        query = str(raw).replace("$job", ".+").replace("$__rate_interval", "1m")
        payload = _http_json(endpoint + "/api/v1/query?" + urlencode({"query": query}))
        results = payload.get("data", {}).get("result", []) if isinstance(payload, dict) else []
        evidence.append({"query": query, "series": len(results)})
        if payload.get("status") != "success" or not results:
            failures.append(f"query returned no series: {query}")
    return OracleResult("prometheus_query", not failures, {"queries": evidence, "failures": failures}, "; ".join(failures) or None)


def _query_export_manifest(config: dict[str, Any], *, workspace: Path, **_: Any) -> OracleResult:
    manifest_path = workspace / str(config["manifest"])
    failures: list[str] = []
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return OracleResult("query_export_manifest", False, {}, f"manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return OracleResult("query_export_manifest", False, {}, f"invalid manifest: {exc}")
    platform = str(config["platform"])
    expected_count = int(config["count"])
    language = str(config["language"])
    if manifest.get("platform") != platform:
        failures.append(f"platform was {manifest.get('platform')!r}")
    if config.get("contract") and manifest.get("contract") != config["contract"]:
        failures.append(f"contract was {manifest.get('contract')!r}")
    if config.get("plugin") and manifest.get("plugin") != config["plugin"]:
        failures.append(f"plugin was {manifest.get('plugin')!r}")
    legacy_serving_keys = {"serving_datasource", "serving_database_name"}.intersection(manifest)
    if legacy_serving_keys:
        failures.append(f"manifest retained profile-level serving mapping: {sorted(legacy_serving_keys)}")
    selection = manifest.get("selection") or {}
    if config.get("selectionMode") and selection.get("mode") != config["selectionMode"]:
        failures.append(f"selection mode was {selection.get('mode')!r}")
    summary = manifest.get("summary") or {}
    if summary != {"total": expected_count, "succeeded": expected_count, "failed": 0}:
        failures.append(f"summary was {summary!r}")
    entries = manifest.get("queries") or []
    if len(entries) != expected_count:
        failures.append(f"manifest contained {len(entries)} queries")
    root = manifest_path.parent
    suffix = str(config.get("suffix") or (".sql" if language == "sql" else f".{language}"))
    files = sorted(path for path in root.glob(f"*{suffix}") if path.is_file() and not path.is_symlink())
    if len(files) != expected_count:
        failures.append(f"found {len(files)} {suffix} files")
    if config.get("forbidSuffix"):
        forbidden = list(root.glob(f"*{config['forbidSuffix']}"))
        if forbidden:
            failures.append(f"found forbidden {config['forbidSuffix']} files")
    required_text = list(config.get("requiredText") or [])
    exported_contents: list[str] = []
    entry_files: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("language") != language or entry.get("status") != "ok":
            failures.append(f"invalid query entry: {entry!r}")
            continue
        relative = entry.get("file")
        path = root / str(relative)
        entry_files.add(str(relative))
        if not path.is_file() or path.is_symlink():
            failures.append(f"query file is missing: {relative}")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        exported_contents.append(content)
        checksum = hashlib.sha256(content.encode()).hexdigest()
        if entry.get("sha256") != checksum:
            failures.append(f"checksum mismatch for {relative}")
        if config.get("contract"):
            if not isinstance(entry.get("id"), str) or not entry["id"]:
                failures.append(f"query entry omitted stable id: {entry!r}")
            if not isinstance(entry.get("candidate_id"), str) or not entry["candidate_id"]:
                failures.append(f"query entry omitted candidate id: {entry!r}")
            if entry.get("sql_file") != relative:
                failures.append(f"canonical SQL file mismatch for {relative}")
            if entry.get("checksum") != f"sha256:{checksum}":
                failures.append(f"canonical checksum mismatch for {relative}")
            if not isinstance(entry.get("name"), str) or not entry["name"]:
                failures.append(f"query entry omitted display name: {entry!r}")
        expected_source_status = config.get("sourceIdentityStatus")
        if expected_source_status:
            source_identity = entry.get("source_identity")
            if not isinstance(source_identity, dict):
                failures.append(f"query entry omitted source identity: {entry!r}")
            elif source_identity.get("status") != expected_source_status:
                failures.append(
                    f"source identity status was {source_identity.get('status')!r} for {relative}"
                )
            elif source_identity.get("provider") != platform:
                failures.append(f"source identity provider was {source_identity.get('provider')!r} for {relative}")
            else:
                forbidden = {
                    "authorization", "connection_uri", "password", "secret",
                    "sqlalchemy_uri", "token", "username",
                }
                stack = [source_identity]
                exposed: set[str] = set()
                while stack:
                    node = stack.pop()
                    if isinstance(node, dict):
                        exposed.update(str(key).lower() for key in node if str(key).lower() in forbidden)
                        stack.extend(node.values())
                    elif isinstance(node, list):
                        stack.extend(node)
                if exposed:
                    failures.append(f"source identity exposed secret fields {sorted(exposed)} for {relative}")
        for expected in required_text:
            if expected not in content:
                failures.append(f"{relative} omitted {expected!r}")
    combined_content = "\n".join(exported_contents)
    for expected in config.get("requiredTextAny") or []:
        if expected not in combined_content:
            failures.append(f"exported files omitted {expected!r}")
    if entry_files != {path.name for path in files}:
        failures.append("manifest file list did not match exported files")
    source_files = sorted((root / "_source").glob("*.json")) if (root / "_source").is_dir() else []
    if not source_files:
        failures.append("no _source JSON files were exported")
    secret_pattern = re.compile(r'(?i)(password|authorization|access[_-]?token|api[_-]?key|clientsecret)')
    leaked = [path.name for path in source_files if secret_pattern.search(path.read_text(encoding="utf-8", errors="replace"))]
    if leaked:
        failures.append(f"source files retained secret fields: {leaked}")
    evidence = {
        "manifest": str(manifest_path.relative_to(workspace)),
        "summary": summary,
        "files": [path.name for path in files],
        "sourceFiles": [path.name for path in source_files],
        "sourceIdentities": [entry.get("source_identity") for entry in entries if isinstance(entry, dict)],
        "failures": failures,
    }
    return OracleResult("query_export_manifest", not failures, evidence, "; ".join(failures) or None)


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
    missing = 0
    while time.monotonic() < deadline:
        result = environment.kubectl(
            ["-n", environment.namespace, "get", "flinkdeployment", deployment, "-o", "json"],
            "oracle-flink-status",
            check=False,
        )
        if result.returncode == 0:
            missing = 0
            try:
                last = json.loads(result.stdout)
            except json.JSONDecodeError:
                last = {}
            state = _nested(last, "status.jobStatus.state")
            if state == "FINISHED":
                return True, last
            if state in {"FAILED", "FAILING"}:
                return False, last
        else:
            missing += 1
            if missing >= 3:
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
                                "-cp", "/opt/flink/lib/*",
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
    "superset_dashboard": _superset_dashboard,
    "postgres_query": _postgres_query,
    "grafana_dashboard": _grafana_dashboard,
    "prometheus_query": _prometheus_query,
    "query_export_manifest": _query_export_manifest,
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
