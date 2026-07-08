"""Output rendering.

Default is ``json`` (indented, non-ASCII preserved) — Statsig payloads are
deeply nested (pulse readouts, warehouse-native configs, generated SQL) and are
consumed both by humans and by the agent's LLM. ``compact`` collapses JSON to a
single line for piping / token thrift; ``table`` / ``plain`` show curated
columns for list results; ``yaml`` is available when installed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

FORMATS = ("json", "compact", "table", "plain", "yaml")
DEFAULT_FORMAT = "json"


def _dump_json(data: Any, compact: bool) -> str:
    if compact:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)
    return str(value)


def _yaml_dump(data: Any) -> str:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - optional
        from .errors import MissingDependencyError

        raise MissingDependencyError(
            "-o yaml needs PyYAML: pip install 'datus-plugin-statsig' already pulls it in"
        ) from exc
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def render(data: Any, fmt: str = DEFAULT_FORMAT) -> str:
    """Render an arbitrary JSON value (object, list, or scalar)."""
    if fmt == "compact":
        return _dump_json(data, compact=True)
    if fmt == "yaml":
        return _yaml_dump(data)
    if fmt in ("table", "plain") and isinstance(data, dict):
        return render_one(data, fmt)
    if fmt in ("table", "plain") and isinstance(data, list):
        return render_rows(data, None, fmt)
    return _dump_json(data, compact=False)


def render_rows(
    rows: List[Dict[str, Any]],
    columns: Optional[Sequence[str]],
    fmt: str = DEFAULT_FORMAT,
) -> str:
    """Render a list of dicts.

    table/plain show `columns` (or the union of keys); json/compact/yaml emit
    the full objects so agents and scripts get every field the API returned.
    """
    if fmt == "json":
        return _dump_json(rows, compact=False)
    if fmt == "compact":
        return _dump_json(rows, compact=True)
    if fmt == "yaml":
        return _yaml_dump(rows)

    rows = [r if isinstance(r, dict) else {"value": r} for r in rows]
    if not columns:
        seen: Dict[str, None] = {}
        for row in rows:
            for key in row:
                seen.setdefault(key)
        columns = list(seen)

    header = list(columns)
    body = [[_cell(row.get(col)) for col in columns] for row in rows]

    if fmt == "plain":
        lines = [" ".join(header)]
        lines.extend(" ".join(cells) for cells in body)
        return "\n".join(lines)

    if not header:
        return "(no rows)"
    widths = [
        max(len(header[i]), *(len(cells[i]) for cells in body)) if body else len(header[i])
        for i in range(len(header))
    ]
    lines = [" | ".join(header[i].ljust(widths[i]) for i in range(len(header)))]
    lines.append("=+=".join("=" * w for w in widths))
    for cells in body:
        lines.append(" | ".join(cells[i].ljust(widths[i]) for i in range(len(header))).rstrip())
    return "\n".join(lines)


def render_one(obj: Dict[str, Any], fmt: str = DEFAULT_FORMAT) -> str:
    """Render a single entity; table mode becomes a two-column property list."""
    if fmt == "json":
        return _dump_json(obj, compact=False)
    if fmt == "compact":
        return _dump_json(obj, compact=True)
    if fmt == "yaml":
        return _yaml_dump(obj)
    rows = [{"property": key, "value": obj[key]} for key in obj]
    return render_rows(rows, ["property", "value"], "plain" if fmt == "plain" else "table")
