"""JSON/YAML/table rendering kept local so this distribution stays independent."""

from __future__ import annotations

import json
from typing import Any

FORMATS = ("json", "yaml", "table")


def render(value: Any, format: str = "json") -> str:
    if format == "yaml":
        import yaml

        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip("\n")
    if format == "table":
        return _table(value)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _table(value: Any) -> str:
    rows = value if isinstance(value, list) else [value]
    if not rows:
        return "(no results)"
    if not all(isinstance(row, dict) for row in rows):
        return json.dumps(value, ensure_ascii=False, default=str)
    keys = list(dict.fromkeys(key for row in rows for key in row))

    def cell(item: Any) -> str:
        return (
            json.dumps(item, ensure_ascii=False, default=str)
            if isinstance(item, (dict, list))
            else str(item or "")
        )

    cells = [[cell(row.get(key, "")) for key in keys] for row in rows]
    widths = [
        max(len(key), *(len(row[i]) for row in cells)) for i, key in enumerate(keys)
    ]
    return "\n".join(
        "  ".join(item.ljust(widths[i]) for i, item in enumerate(row))
        for row in [keys, *cells]
    )
