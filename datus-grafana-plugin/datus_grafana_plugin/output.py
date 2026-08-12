from __future__ import annotations

import json
from typing import Any

FORMATS = ("json", "yaml", "table")


def render(data: Any, fmt: str = "json") -> str:
    if fmt == "yaml":
        import yaml
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if fmt != "table":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    rows = data if isinstance(data, list) else [data]
    rows = [row if isinstance(row, dict) else {"value": row} for row in rows]
    if not rows:
        return "(no rows)"
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    def cell(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str) if isinstance(value, (dict, list)) else str(value or "")
    values = [[cell(row.get(column)) for column in columns] for row in rows]
    widths = [max(len(column), *(len(row[i]) for row in values)) for i, column in enumerate(columns)]
    return "\n".join([
        " | ".join(column.ljust(widths[i]) for i, column in enumerate(columns)),
        "-+-".join("-" * width for width in widths),
        *(" | ".join(row[i].ljust(widths[i]) for i in range(len(columns))).rstrip() for row in values),
    ])
