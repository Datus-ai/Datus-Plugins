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

from .errors import PluginError, UsageError

_SECRET_KEYS = {
    "access_token", "refresh_token", "password", "authorization", "cookie",
    "encrypted_extra", "server_cert", "ssh_tunnel", "impersonate_user",
}


def export_dashboard(
    client: Any,
    dashboard_id: str,
    *,
    output_root: str = "reference_sql",
    include_hidden: bool = False,
    overwrite: bool = False,
    instance_url: str,
    profile_name: str | None = None,
) -> dict[str, Any]:
    root = _workspace_path(output_root)
    dashboard = _result(client.request("GET", f"/api/v1/dashboard/{dashboard_id}"))
    title = dashboard.get("dashboard_title") or dashboard.get("title") or str(dashboard_id)
    target = root / "superset" / _slug(title, fallback=str(dashboard_id))
    _guard_target(target, overwrite)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    queries: list[dict[str, Any]] = []
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
        for chart_summary in charts:
            if not isinstance(chart_summary, dict):
                continue
            chart_id = chart_summary.get("id") or chart_summary.get("slice_id")
            if chart_id is None or str(chart_id) in seen:
                continue
            seen.add(str(chart_id))
            hidden = bool(chart_summary.get("is_hidden") or chart_summary.get("hidden"))
            if hidden and not include_hidden:
                continue
            try:
                detail = _result(client.request("GET", f"/api/v1/chart/{chart_id}"))
                _write_json(source_dir / f"chart-{chart_id}.json", _redact(detail))
                sqls = _chart_sql(client, chart_id, detail)
                if not sqls:
                    failures += 1
                    queries.append(_failed_entry(chart_id, detail, "no compiled SQL returned by Superset"))
                    continue
                for index, sql in enumerate(sqls, 1):
                    title_value = detail.get("slice_name") or chart_summary.get("slice_name") or f"chart-{chart_id}"
                    filename = f"{chart_id}-{_slug(title_value)}-q{index}.sql"
                    text = _sql_document(title, title_value, sql)
                    _write_text(staging / filename, text)
                    queries.append(
                        {
                            "asset_type": "chart",
                            "asset_id": chart_id,
                            "asset_title": title_value,
                            "query_index": index,
                            "language": "sql",
                            "datasource": _datasource(detail),
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

        manifest = {
            "schema_version": 1,
            "platform": "superset",
            "profile": profile_name,
            "instance_url": instance_url,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "dashboard": {
                "id": dashboard.get("id", dashboard_id),
                "title": title,
                "slug": dashboard.get("slug"),
                "version": dashboard.get("version"),
                "time_range": _time_range(dashboard),
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


def _chart_sql(client: Any, chart_id: Any, detail: dict[str, Any]) -> list[str]:
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
    for context in contexts:
        payload = dict(context)
        payload["result_format"] = "json"
        payload["result_type"] = "query"
        response = client.request("POST", "/api/v1/chart/data", json_body=payload)
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
    return {
        "asset_type": "chart", "asset_id": chart_id,
        "asset_title": chart.get("slice_name") or chart.get("name") or str(chart_id),
        "language": "sql", "file": None, "sha256": None, "status": "failed", "error": error,
    }


def _datasource(detail: dict[str, Any]) -> Any:
    for source in (detail, detail.get("result") if isinstance(detail.get("result"), dict) else {}):
        for key in ("datasource", "datasource_id"):
            if source.get(key) is not None:
                return source[key]
    return None


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
