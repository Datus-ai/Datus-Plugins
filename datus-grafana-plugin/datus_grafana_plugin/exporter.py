"""Export every Grafana dashboard target while retaining query templates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import PluginError, UsageError

SECRET_KEYS = {
    "password", "basicAuthPassword", "secureJsonData", "secureJsonFields",
    "authorization", "token", "apiKey", "accessToken", "clientSecret",
}
SECRET_KEYS_LOWER = {key.lower() for key in SECRET_KEYS}


def export_dashboard(
    client: Any, uid: str, *, output_root: str = "reference_sql",
    include_hidden: bool = False, overwrite: bool = False,
    instance_url: str, profile_name: str | None = None,
    default_datasource_uid: str | None = None,
) -> dict[str, Any]:
    payload = client.dashboard_request("GET", uid)
    dashboard, metadata = _dashboard_document(payload)
    title = dashboard.get("title") or uid
    root = _workspace_path(output_root)
    target_dir = root / "grafana" / _slug(title, uid)
    if target_dir.exists() and not overwrite:
        raise UsageError(f"output directory already exists: {target_dir}; pass --overwrite to replace it")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}-", dir=target_dir.parent))
    entries: list[dict[str, Any]] = []
    datasource_cache: dict[str, dict[str, Any]] = {}
    try:
        source = staging / "_source"
        source.mkdir()
        _write_json(source / "dashboard.json", _redact(payload))
        variables = _dashboard_variables(dashboard)
        for panel in _panels(dashboard.get("panels") or []):
            if panel.get("libraryPanel"):
                panel = _resolve_library_panel(client, panel, source)
            hidden = bool(panel.get("hide") or panel.get("transparent") and panel.get("collapsed"))
            if hidden and not include_hidden:
                continue
            panel_ds = _datasource_ref(panel.get("datasource"), default_datasource_uid)
            targets = panel.get("targets") or []
            for index, query in enumerate(targets, 1):
                if not isinstance(query, dict):
                    continue
                if query.get("hide") and not include_hidden:
                    continue
                ds_ref = _datasource_ref(query.get("datasource"), panel_ds.get("uid") if panel_ds else default_datasource_uid)
                ds = _datasource(client, ds_ref, datasource_cache)
                language, content, suffix = classify_query(query, ds)
                ref_id = str(query.get("refId") or index)
                prefix = f"{panel.get('id', 'panel')}-{_slug(panel.get('title') or 'panel')}-{_slug(ref_id)}"
                filename = prefix + suffix
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2, default=str) + "\n"
                if language == "sql":
                    text = _sql_document(title, panel.get("title") or str(panel.get("id")), ref_id, text)
                _write_text(staging / filename, text)
                entries.append(
                    {
                        "asset_type": "panel", "asset_id": panel.get("id"),
                        "asset_title": panel.get("title"), "ref_id": ref_id,
                        "query_index": index, "language": language,
                        "datasource": ds_ref or ds, "file": filename,
                        "hidden": bool(query.get("hide") or hidden),
                        "variables": sorted(set(_variables(text))),
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "status": "ok", "error": None,
                    }
                )
        manifest = {
            "schema_version": 1, "platform": "grafana", "profile": profile_name,
            "instance_url": instance_url, "exported_at": datetime.now(timezone.utc).isoformat(),
            "dashboard": {
                "uid": uid, "title": title, "version": dashboard.get("version"),
                "resource_version": metadata.get("resourceVersion"),
                "time_range": dashboard.get("time"), "timezone": dashboard.get("timezone"),
                "variables": variables,
            },
            "queries": entries,
            "summary": {"total": len(entries), "succeeded": len(entries), "failed": 0},
        }
        _write_json(staging / "manifest.json", manifest)
        if not entries:
            raise PluginError("dashboard contains no exportable query targets")
        _commit(staging, target_dir, overwrite)
        return {"output_dir": str(target_dir), **manifest["summary"]}
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def classify_query(query: dict[str, Any], datasource: dict[str, Any] | None) -> tuple[str, Any, str]:
    query_datasource = query.get("datasource")
    query_ds_type = query_datasource.get("type") if isinstance(query_datasource, dict) else None
    query_ds_uid = query_datasource.get("uid") if isinstance(query_datasource, dict) else query_datasource
    ds_type = str((datasource or {}).get("type") or query_ds_type or "").lower()
    if query.get("type") == "math" or query_ds_uid == "__expr__":
        return "grafana-expression", _redact(query), ".expr.json"
    raw_sql = query.get("rawSql") or query.get("rawSQL") or query.get("sql")
    if isinstance(raw_sql, str) and raw_sql.strip():
        return "sql", raw_sql.strip(), ".sql"
    expr = query.get("expr")
    if isinstance(expr, str) and expr.strip():
        if "loki" in ds_type:
            return "logql", expr.strip() + "\n", ".logql"
        if "tempo" in ds_type or "trace" in ds_type:
            return "traceql", expr.strip() + "\n", ".traceql"
        return "promql", expr.strip() + "\n", ".promql"
    flux = query.get("query")
    if isinstance(flux, str) and flux.strip():
        if query.get("queryType") == "flux" or "flux" in ds_type:
            return "flux", flux.strip() + "\n", ".flux"
        if "influx" in ds_type:
            return "influxql", flux.strip() + "\n", ".influxql"
        if "graphite" in ds_type:
            return "graphite", flux.strip() + "\n", ".graphite"
    target = query.get("target")
    if isinstance(target, str) and target.strip() and "graphite" in ds_type:
        return "graphite", target.strip() + "\n", ".graphite"
    return "unknown", _redact(query), ".query.json"


def _dashboard_document(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}, {}
    if isinstance(payload.get("dashboard"), dict):
        return payload["dashboard"], payload.get("meta") or {}
    if isinstance(payload.get("spec"), dict):
        return payload["spec"], payload.get("metadata") or {}
    return payload, {}


def _panels(values: list[Any]) -> Iterator[dict[str, Any]]:
    for panel in values:
        if not isinstance(panel, dict):
            continue
        yield panel
        nested = panel.get("panels")
        if isinstance(nested, list):
            yield from _panels(nested)


def _resolve_library_panel(client: Any, panel: dict[str, Any], source: Path) -> dict[str, Any]:
    uid = (panel.get("libraryPanel") or {}).get("uid")
    if not uid:
        return panel
    try:
        response = client.request("GET", f"/api/library-elements/{uid}") or {}
        _write_json(source / f"library-{_slug(uid)}.json", _redact(response))
        result = response.get("result", response)
        model = result.get("model") if isinstance(result, dict) else None
        if isinstance(model, dict):
            merged = dict(model)
            merged.setdefault("id", panel.get("id"))
            merged.setdefault("title", panel.get("title") or result.get("name"))
            return merged
    except Exception:
        pass
    return panel


def _datasource_ref(value: Any, fallback_uid: str | None) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {key: value.get(key) for key in ("uid", "type") if value.get(key) is not None}
    if isinstance(value, str) and value and not value.startswith("$"):
        return {"uid": value}
    return {"uid": fallback_uid} if fallback_uid else None


def _datasource(client: Any, ref: dict[str, Any] | None, cache: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not ref:
        return None
    uid = ref.get("uid")
    if not uid or uid in {"-- Mixed --", "__expr__"} or str(uid).startswith("$"):
        return ref
    if uid not in cache:
        try:
            value = client.request("GET", f"/api/datasources/uid/{uid}")
            cache[uid] = _redact(value) if isinstance(value, dict) else ref
        except Exception:
            cache[uid] = ref
    return cache[uid]


def _dashboard_variables(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    values = ((dashboard.get("templating") or {}).get("list") or [])
    return [_redact(v) for v in values if isinstance(v, dict)]


def _variables(text: str) -> list[str]:
    found: set[str] = set()
    for match in re.finditer(r"\$\{([A-Za-z_][\w]*)[^}]*\}|\$([A-Za-z_][\w]*)", text):
        found.add(match.group(1) or match.group(2))
    return list(found)


def _sql_document(dashboard: str, panel: str, ref_id: str, sql: str) -> str:
    body = sql.strip()
    if not body.endswith(";"):
        body += ";"
    return f"-- Dashboard={_comment(dashboard)};\n-- Panel={_comment(panel)};\n-- RefId={_comment(ref_id)};\n{body}\n"


def _comment(value: Any) -> str:
    return re.sub(r"[\r\n]+", " ", str(value)).replace(";", ",").strip()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(child) for key, child in value.items() if key.lower() not in SECRET_KEYS_LOWER}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value


def _slug(value: Any, fallback: str = "query") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return result[:80] or fallback


def _workspace_path(raw: str) -> Path:
    cwd = Path.cwd().resolve()
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else cwd / candidate).resolve()
    if resolved != cwd and cwd not in resolved.parents:
        raise UsageError("output root must be inside the current project workspace")
    return resolved


def _commit(staging: Path, target: Path, overwrite: bool) -> None:
    backup = None
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
