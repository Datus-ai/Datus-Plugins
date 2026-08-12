"""JSON/YAML/table output kept local so this distribution is independent."""

from __future__ import annotations

import json
from typing import Any

FORMATS = ("json", "yaml", "table")


def render(data: Any, fmt: str = "json") -> str:
    if fmt == "yaml":
        import yaml

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if fmt == "table":
        return _table(data)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _table(data: Any) -> str:
    rows = data if isinstance(data, list) else [data]
    rows = [row if isinstance(row, dict) else {"value": row} for row in rows]
    if not rows:
        return "(no rows)"
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    values = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [max(len(column), *(len(row[i]) for row in values)) for i, column in enumerate(columns)]
    lines = [" | ".join(column.ljust(widths[i]) for i, column in enumerate(columns))]
    lines.append("-+-".join("-" * width for width in widths))
    lines.extend(" | ".join(row[i].ljust(widths[i]) for i in range(len(columns))).rstrip() for row in values)
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value)
