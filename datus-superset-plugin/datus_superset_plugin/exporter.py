"""Export dashboard query assets without importing Datus or its BI adapters."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from .errors import PluginError, UsageError

_SECRET_KEYS = {
    "access_token", "refresh_token", "password", "authorization", "cookie",
    "encrypted_extra", "server_cert", "ssh_tunnel", "impersonate_user",
    "sqlalchemy_uri", "connection_uri",
}


def discover_dashboard_candidates(client: Any, dashboard_id: str) -> dict[str, Any]:
    """Return stable chart candidates with query-level, credential-free source identities."""
    dashboard = _result(client.request("GET", f"/api/v1/dashboard/{dashboard_id}"))
    title = dashboard.get("dashboard_title") or dashboard.get("title") or str(dashboard_id)
    raw_charts = _result(client.request("GET", f"/api/v1/dashboard/{dashboard_id}/charts"))
    charts = raw_charts if isinstance(raw_charts, list) else raw_charts.get("result", [])
    if not isinstance(charts, list):
        charts = []
    dataset_cache: dict[str, dict[str, Any] | None] = {}
    database_cache: dict[str, dict[str, Any] | None] = {}
    connection_cache: dict[str, dict[str, Any] | None] = {}
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for summary in charts:
        if not isinstance(summary, dict):
            continue
        chart_id = summary.get("id") or summary.get("slice_id")
        chart_key = str(chart_id)
        if chart_id is None or chart_key in seen:
            continue
        seen.add(chart_key)
        try:
            detail = _result(client.request("GET", f"/api/v1/chart/{chart_id}"))
            form_data = summary.get("form_data")
            source_identity = _source_identity(
                client,
                detail,
                form_data,
                dataset_cache=dataset_cache,
                database_cache=database_cache,
                connection_cache=connection_cache,
            )
            error = None
        except Exception as exc:
            detail = {}
            source_identity = {
                "provider": "superset",
                "status": "unresolved",
                "reason": "chart or datasource metadata is unavailable",
            }
            error = str(exc)
        candidates.append(
            _compact(
                {
                    "id": _candidate_id(chart_id),
                    "name": detail.get("slice_name") or summary.get("slice_name") or f"chart-{chart_id}",
                    "description": detail.get("description") or summary.get("description"),
                    "hidden": bool(summary.get("is_hidden") or summary.get("hidden")),
                    "exportable": error is None,
                    "source_identity": source_identity,
                    "plugin_metadata": {"asset_type": "chart", "asset_id": chart_id},
                    "error": error,
                }
            )
        )
    return {
        "plugin": "superset",
        "dashboard": {"id": dashboard.get("id", dashboard_id), "name": title},
        "candidates": candidates,
    }


def export_dashboard(
    client: Any,
    dashboard_id: str,
    *,
    output_root: str = "reference_sql",
    chart_ids: Iterable[str] | None = None,
    include_hidden: bool = False,
    overwrite: bool = False,
    instance_url: str,
    profile_name: str | None = None,
) -> dict[str, Any]:
    requested_chart_ids = {str(value) for value in chart_ids or [] if str(value).strip()}
    root = _workspace_path(output_root)
    dashboard = _result(client.request("GET", f"/api/v1/dashboard/{dashboard_id}"))
    title = dashboard.get("dashboard_title") or dashboard.get("title") or str(dashboard_id)
    target = root / "superset" / _slug(title, fallback=str(dashboard_id))
    _guard_target(target, overwrite)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    queries: list[dict[str, Any]] = []
    dataset_cache: dict[str, dict[str, Any] | None] = {}
    database_cache: dict[str, dict[str, Any] | None] = {}
    connection_cache: dict[str, dict[str, Any] | None] = {}
    failures = 0
    try:
        source_dir = staging / "_source"
        source_dir.mkdir()
        _write_json(source_dir / "dashboard.json", _redact(dashboard))
        charts_payload = client.request("GET", f"/api/v1/dashboard/{dashboard_id}/charts") or {}
        charts = charts_payload.get("result", charts_payload) if isinstance(charts_payload, dict) else charts_payload
        if not isinstance(charts, list):
            charts = []
        seen: set[str] = set()
        matched: set[str] = set()
        for chart_summary in charts:
            if not isinstance(chart_summary, dict):
                continue
            chart_id = chart_summary.get("id") or chart_summary.get("slice_id")
            chart_key = str(chart_id)
            if chart_id is None or chart_key in seen:
                continue
            seen.add(chart_key)
            if requested_chart_ids and chart_key not in requested_chart_ids:
                continue
            matched.add(chart_key)
            hidden = bool(chart_summary.get("is_hidden") or chart_summary.get("hidden"))
            if hidden and not include_hidden:
                if requested_chart_ids:
                    failures += 1
                    queries.append(
                        _failed_entry(chart_id, chart_summary, "selected chart is hidden; pass --include-hidden")
                    )
                continue
            try:
                detail = _result(client.request("GET", f"/api/v1/chart/{chart_id}"))
                _write_json(source_dir / f"chart-{chart_id}.json", _redact(detail))
                form_data = chart_summary.get("form_data")
                sqls = _chart_sql(client, chart_id, detail, form_data)
                if not sqls:
                    failures += 1
                    queries.append(_failed_entry(chart_id, detail, "no compiled SQL returned by Superset"))
                    continue
                source_identity = _source_identity(
                    client,
                    detail,
                    form_data,
                    dataset_cache=dataset_cache,
                    database_cache=database_cache,
                    connection_cache=connection_cache,
                )
                for index, sql in enumerate(sqls, 1):
                    title_value = detail.get("slice_name") or chart_summary.get("slice_name") or f"chart-{chart_id}"
                    filename = f"{chart_id}-{_slug(title_value)}-q{index}.sql"
                    text = _sql_document(title, title_value, sql)
                    _write_text(staging / filename, text)
                    queries.append(
                        {
                            "id": _query_id(chart_id, index),
                            "candidate_id": _candidate_id(chart_id),
                            "name": title_value,
                            "description": detail.get("description") or chart_summary.get("description"),
                            "sql_file": filename,
                            "checksum": f"sha256:{_sha256(text)}",
                            "asset_type": "chart",
                            "asset_id": chart_id,
                            "asset_title": title_value,
                            "query_index": index,
                            "language": "sql",
                            "datasource": _datasource(detail, form_data),
                            "source_identity": source_identity,
                            "file": filename,
                            "hidden": hidden,
                            "variables": _variables(detail),
                            "sha256": _sha256(text),
                            "status": "ok",
                            "error": None,
                            "compiled_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
            except Exception as exc:
                failures += 1
                queries.append(_failed_entry(chart_id, chart_summary, str(exc)))

        for missing_chart_id in sorted(requested_chart_ids - matched):
            failures += 1
            queries.append(_failed_entry(missing_chart_id, {}, "selected chart is not part of this dashboard"))

        manifest = {
            "contract": "dashboard-sql-export/v1",
            "schema_version": 1,
            "plugin": "superset",
            "platform": "superset",
            "profile": profile_name,
            "instance_url": instance_url,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "dashboard": {
                "id": dashboard.get("id", dashboard_id),
                "name": title,
                "title": title,
                "slug": dashboard.get("slug"),
                "version": dashboard.get("version"),
                "time_range": _time_range(dashboard),
            },
            "selection": {
                "mode": "selective" if requested_chart_ids else "full-dashboard",
                "chart_ids": sorted(requested_chart_ids),
            },
            "queries": queries,
            "summary": {
                "total": len(queries),
                "succeeded": sum(q["status"] == "ok" for q in queries),
                "failed": sum(q["status"] != "ok" for q in queries),
            },
        }
        _write_json(staging / "manifest.json", manifest)
        if not any(q["status"] == "ok" for q in queries):
            raise PluginError("no dashboard query could be exported")
        _commit(staging, target, overwrite)
        return {"output_dir": str(target), **manifest["summary"]}
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _chart_sql(
    client: Any, chart_id: Any, detail: dict[str, Any], form_data: Any = None
) -> list[str]:
    contexts: list[dict[str, Any]] = []
    for key in ("query_context",):
        value = _jsonish(detail.get(key))
        if isinstance(value, dict):
            contexts.append(value)
    result = detail.get("result")
    if isinstance(result, dict):
        value = _jsonish(result.get("query_context"))
        if isinstance(value, dict):
            contexts.append(value)
    # Superset only writes query_context when a chart is saved from Explore, so charts
    # created by load_examples, dashboard import, or the API have none. Rebuild one from
    # the chart's form_data as a last resort.
    synthesized = _synthesized_context(form_data)
    if synthesized:
        contexts.append(synthesized)
    for context in contexts:
        payload = dict(context)
        payload["result_format"] = "json"
        payload["result_type"] = "query"
        try:
            response = client.request("POST", "/api/v1/chart/data", json_body=payload)
        except Exception:
            continue
        found = _find_sql(response)
        if found:
            return found
    try:
        response = client.request("GET", f"/api/v1/chart/{chart_id}/data/")
        found = _find_sql(response)
        if found:
            return found
    except Exception:
        pass
    return _find_sql(detail)


# form_data keys that name grouping//dimension columns across the built-in viz types.
_COLUMN_KEYS = (
    "groupby", "groupbyColumns", "groupbyRows", "columns",
    "all_columns", "series", "entity",
)


def _synthesized_context(form_data: Any) -> dict[str, Any] | None:
    """Build a minimal query_context from form_data.

    This is deliberately viz-agnostic: Superset's real form_data -> query_context
    conversion lives in each frontend viz plugin's buildQuery, which has no backend
    equivalent. Charts whose form_data carries no resolvable datasource (or whose viz
    stores its dimensions under bespoke keys, such as the deck.gl family) are left to
    the caller to report as failures.
    """
    form_data = _jsonish(form_data)
    if not isinstance(form_data, dict):
        return None
    raw = str(form_data.get("datasource") or "")
    identifier, _, kind = raw.partition("__")
    if not identifier.isdigit():
        return None
    metrics = form_data.get("metrics")
    if not isinstance(metrics, list):
        metrics = [form_data["metric"]] if form_data.get("metric") else []
    columns: list[Any] = []
    for key in _COLUMN_KEYS:
        value = form_data.get(key)
        for item in value if isinstance(value, list) else [value]:
            if item and item not in columns:
                columns.append(item)
    if not metrics and not columns:
        return None
    return {
        "datasource": {"id": int(identifier), "type": kind or "table"},
        "queries": [{
            "columns": columns,
            "metrics": metrics,
            "filters": [],
            "orderby": [],
            "row_limit": form_data.get("row_limit") or 1000,
        }],
    }


def _find_sql(value: Any) -> list[str]:
    found: list[str] = []
    def visit(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                if child_key.lower() in {"query", "sql", "sql_query"} and isinstance(child, str):
                    stripped = child.strip()
                    if _looks_like_sql(stripped) and stripped not in found:
                        found.append(stripped)
                else:
                    visit(child, child_key)
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
    visit(value)
    return found


def _looks_like_sql(value: str) -> bool:
    return bool(re.match(r"^(select|with|show|explain)\b", value, re.I | re.S))


def _failed_entry(chart_id: Any, chart: dict[str, Any], error: str) -> dict[str, Any]:
    title = chart.get("slice_name") or chart.get("name") or str(chart_id)
    return {
        "id": _query_id(chart_id),
        "candidate_id": _candidate_id(chart_id),
        "name": title,
        "description": chart.get("description"),
        "sql_file": None,
        "checksum": None,
        "asset_type": "chart", "asset_id": chart_id,
        "asset_title": title,
        "language": "sql", "file": None, "sha256": None, "status": "failed", "error": error,
    }


def _candidate_id(chart_id: Any) -> str:
    return f"chart-{chart_id}"


def _query_id(chart_id: Any, query_index: int | None = None) -> str:
    suffix = str(query_index) if query_index is not None else "unknown"
    return f"{_candidate_id(chart_id)}-query-{suffix}"


def _datasource(detail: dict[str, Any], form_data: Any = None) -> Any:
    for source in (detail, detail.get("result") if isinstance(detail.get("result"), dict) else {}):
        for key in ("datasource", "datasource_id"):
            if source.get(key) is not None:
                return source[key]
    # Superset's chart show response omits the datasource entirely; form_data carries it
    # as "<id>__<type>", which reads as "None__table" when the chart has lost its dataset.
    form_data = _jsonish(form_data)
    if isinstance(form_data, dict):
        raw = str(form_data.get("datasource") or "")
        if raw.partition("__")[0].isdigit():
            return raw
    return None


def _source_identity(
    client: Any,
    detail: dict[str, Any],
    form_data: Any,
    *,
    dataset_cache: dict[str, dict[str, Any] | None],
    database_cache: dict[str, dict[str, Any] | None],
    connection_cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    """Resolve one chart's Superset Dataset and Database without leaking credentials."""
    ref = _datasource_ref(_datasource(detail, form_data))
    identity: dict[str, Any] = {
        "provider": "superset",
        "status": "unresolved",
        "datasource": ref,
    }
    dataset_id = ref.get("id") if ref else None
    if dataset_id is None or str(ref.get("type") or "table") != "table":
        identity["reason"] = "chart datasource is not a physical Superset dataset"
        return identity

    dataset = _cached_result(client, f"/api/v1/dataset/{dataset_id}", dataset_cache, str(dataset_id))
    if dataset is None:
        identity["reason"] = "Superset dataset metadata is unavailable"
        return identity
    identity["dataset"] = _compact(
        {
            "id": dataset.get("id", dataset_id),
            "name": dataset.get("table_name") or dataset.get("datasource_name"),
            "schema": dataset.get("schema"),
            "type": ref.get("type") or "table",
        }
    )

    database_value = dataset.get("database")
    database_id = database_value.get("id") if isinstance(database_value, dict) else None
    database_id = database_id or dataset.get("database_id")
    if database_id is None:
        identity["status"] = "partial"
        identity["reason"] = "Superset dataset omitted its Database identity"
        return identity

    database = _cached_result(client, f"/api/v1/database/{database_id}", database_cache, str(database_id)) or {}
    connection = _cached_result(
        client,
        f"/api/v1/database/{database_id}/connection",
        connection_cache,
        str(database_id),
    ) or {}
    database_summary = {
        "id": database.get("id", database_id),
        "name": database.get("database_name")
        or (database_value.get("database_name") if isinstance(database_value, dict) else None),
        "backend": database.get("backend")
        or (database_value.get("backend") if isinstance(database_value, dict) else None),
    }
    identity["database"] = _compact(database_summary)
    fingerprint = _connection_fingerprint(database, connection)
    if fingerprint:
        identity["connection"] = fingerprint
    if fingerprint.get("backend") and (fingerprint.get("database") or fingerprint.get("path")):
        identity["status"] = "resolved"
    else:
        identity["status"] = "partial"
        identity["reason"] = "Superset Database connection metadata is incomplete"
    return identity


def _datasource_ref(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        identifier = value.get("id") or value.get("datasource_id")
        kind = value.get("type") or value.get("datasource_type") or "table"
    elif isinstance(value, int):
        identifier, kind = value, "table"
    else:
        identifier, _, kind = str(value or "").partition("__")
        if not identifier.isdigit():
            return None
        identifier = int(identifier)
        kind = kind or "table"
    if identifier is None:
        return None
    return {"id": identifier, "type": str(kind)}


def _cached_result(
    client: Any,
    path: str,
    cache: dict[str, dict[str, Any] | None],
    key: str,
) -> dict[str, Any] | None:
    if key not in cache:
        try:
            value = _result(client.request("GET", path))
            cache[key] = value or None
        except Exception:
            cache[key] = None
    return cache[key]


def _connection_fingerprint(database: dict[str, Any], connection: dict[str, Any]) -> dict[str, Any]:
    payload = _result(connection)
    parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
    uri = payload.get("sqlalchemy_uri") or database.get("sqlalchemy_uri")
    parsed = _parse_connection_uri(uri)
    backend = (
        parsed.get("backend")
        or parameters.get("engine")
        or parameters.get("backend")
        or database.get("backend")
    )
    fingerprint = {
        "backend": _canonical_backend(backend),
        "driver": parsed.get("driver"),
        "host": parsed.get("host") or parameters.get("host"),
        "port": parsed.get("port") or _port(parameters.get("port")),
        "database": parsed.get("database")
        or parameters.get("database")
        or parameters.get("dbname"),
        "path": parsed.get("path"),
    }
    return _compact(fingerprint)


def _parse_connection_uri(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or "://" not in value:
        return {}
    try:
        parsed = urlsplit(value)
        scheme, _, driver = parsed.scheme.partition("+")
        backend = _canonical_backend(scheme)
        if backend in {"sqlite", "duckdb"}:
            return _compact(
                {
                    "backend": backend,
                    "driver": driver or None,
                    "path": unquote(parsed.path) or None,
                }
            )
        return _compact(
            {
                "backend": backend,
                "driver": driver or None,
                "host": parsed.hostname,
                "port": parsed.port,
                "database": unquote(parsed.path.lstrip("/")) or None,
            }
        )
    except (TypeError, ValueError):
        return {}


def _canonical_backend(value: Any) -> str | None:
    backend = str(value or "").strip().lower()
    aliases = {"postgres": "postgresql", "pgsql": "postgresql"}
    return aliases.get(backend, backend) or None


def _port(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if child not in (None, "")}


def _variables(detail: dict[str, Any]) -> list[str]:
    text = json.dumps(detail, ensure_ascii=False, default=str)
    found: set[str] = set()
    for match in re.finditer(r"\$\{([A-Za-z_][\w]*)[^}]*\}|\$([A-Za-z_][\w]*)", text):
        found.add(match.group(1) or match.group(2))
    return sorted(found)


def _time_range(dashboard: dict[str, Any]) -> Any:
    metadata = _jsonish(dashboard.get("json_metadata"))
    return metadata.get("timed_refresh_immune_slices") if isinstance(metadata, dict) else None


def _sql_document(dashboard: str, chart: str, sql: str) -> str:
    body = sql.strip()
    if not body.endswith(";"):
        body += ";"
    return f"-- Dashboard={_comment(dashboard)};\n-- Chart={_comment(chart)};\n{body}\n"


def _comment(value: Any) -> str:
    return re.sub(r"[\r\n]+", " ", str(value)).replace(";", ",").strip()


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        return value["result"]
    return value if isinstance(value, dict) else {}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(child) for key, child in value.items() if key.lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value


def _slug(value: Any, fallback: str = "dashboard") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:80] or fallback


def _workspace_path(raw: str) -> Path:
    cwd = Path.cwd().resolve()
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else cwd / candidate).resolve()
    if resolved != cwd and cwd not in resolved.parents:
        raise UsageError("output root must be inside the current project workspace")
    return resolved


def _guard_target(target: Path, overwrite: bool) -> None:
    if target.exists() and not overwrite:
        raise UsageError(f"output directory already exists: {target}; pass --overwrite to replace it")


def _commit(staging: Path, target: Path, overwrite: bool) -> None:
    backup: Path | None = None
    if target.exists():
        if not overwrite:
            raise UsageError(f"output directory already exists: {target}")
        backup = target.with_name(f".{target.name}.backup-{os.getpid()}")
        target.rename(backup)
    try:
        staging.rename(target)
    except Exception:
        if backup and backup.exists():
            backup.rename(target)
        raise
    if backup and backup.exists():
        shutil.rmtree(backup)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
