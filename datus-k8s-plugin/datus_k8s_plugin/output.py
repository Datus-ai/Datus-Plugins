"""kubectl-like common output formats."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

FORMATS = ("table", "wide", "json", "yaml", "name")


def plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def objects(value: Any) -> list[dict[str, Any]]:
    data = plain(value)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return [data] if isinstance(data, dict) else []


def _age(timestamp: Any) -> str:
    if not timestamp:
        return "<unknown>"
    try:
        created = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
    except (TypeError, ValueError):
        return str(timestamp)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def render(value: Any, fmt: str = "table", kind: str | None = None) -> str:
    data = plain(value)
    rows = objects(data)
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if fmt == "yaml":
        import yaml

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip()
    if fmt == "name":
        lines = []
        for row in rows:
            meta = row.get("metadata") or {}
            item_kind = str(row.get("kind") or kind or "resource").lower()
            lines.append(f"{item_kind}/{meta.get('name', '<unknown>')}")
        return "\n".join(lines)

    headers = ["NAME", "STATUS", "AGE"]
    if fmt == "wide":
        headers.extend(["NAMESPACE", "API VERSION", "KIND"])
    table: list[list[str]] = []
    for row in rows:
        meta = row.get("metadata") or {}
        status = row.get("status") or {}
        phase = status.get("phase") or status.get("reason")
        if not phase:
            conditions = status.get("conditions") or []
            ready = next((c for c in conditions if c.get("type") in {"Ready", "Available"}), None)
            phase = ready.get("status") if ready else ""
        cells = [
            str(meta.get("name") or "<unknown>"),
            str(phase or ""),
            _age(meta.get("creationTimestamp") or meta.get("creation_timestamp")),
        ]
        if fmt == "wide":
            cells.extend(
                [
                    str(meta.get("namespace") or ""),
                    str(row.get("apiVersion") or row.get("api_version") or ""),
                    str(row.get("kind") or kind or ""),
                ]
            )
        table.append(cells)
    widths = [len(h) for h in headers]
    for row in table:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in table)
    return "\n".join(lines)


def print_rendered(value: Any, fmt: str = "table", kind: str | None = None) -> None:
    text = render(value, fmt, kind)
    if text:
        print(text)


def resource_list(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {"apiVersion": "v1", "kind": "List", "items": list(items)}
