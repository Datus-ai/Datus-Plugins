"""Output rendering: table / plain (selected columns), json / yaml (full objects)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

FORMATS = ("table", "json", "yaml", "plain")
DEFAULT_FORMAT = "table"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


def _yaml_dump(data: Any) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def render_rows(
    rows: List[Dict[str, Any]],
    columns: Optional[Sequence[str]],
    fmt: str = DEFAULT_FORMAT,
) -> str:
    """Render a list of dicts.

    table/plain show `columns` (or the union of keys); json/yaml emit the
    full objects so agents and scripts get every field the API returned.
    """
    if fmt == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False, default=str)
    if fmt == "yaml":
        return _yaml_dump(rows)

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
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    if fmt == "yaml":
        return _yaml_dump(obj)
    rows = [{"property": key, "value": obj[key]} for key in obj]
    return render_rows(rows, ["property", "value"], "plain" if fmt == "plain" else "table")
