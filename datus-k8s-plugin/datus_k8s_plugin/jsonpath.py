"""The kubectl-style JSONPath subset used by ``-o jsonpath=``."""

from __future__ import annotations

import re
from typing import Any

from .errors import UsageError

_STEP = re.compile(r"\.(?P<field>[A-Za-z0-9_-]+)|\[(?P<index>\d+)\]")


def resolve(item: dict[str, Any], expression: str) -> Any:
    """Resolve a kubectl-style ``{.a.b[0].c}`` expression against one object.

    Only field and list-index steps are supported — enough to read any status
    field, which is what focused status output needs. Filters, wildcards, ranges,
    and escaped dots are rejected rather than silently mis-evaluated. A step that
    runs off the end of the object yields ``None`` (the field simply is not there
    yet).
    """
    body = expression.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1].strip()
    if not body.startswith("."):
        raise UsageError(
            f"unsupported jsonpath {expression!r}: it must start with '.', as in {{.status.phase}}"
        )
    current: Any = item
    position = 0
    while position < len(body):
        step = _STEP.match(body, position)
        if step is None:
            raise UsageError(
                f"unsupported jsonpath {expression!r}: only .field and [index] steps are supported"
            )
        position = step.end()
        field, index = step.group("field"), step.group("index")
        if field is not None:
            if not isinstance(current, dict):
                return None
            current = current.get(field)
        else:
            if not isinstance(current, list) or int(index) >= len(current):
                return None
            current = current[int(index)]
    return current


def display(value: Any) -> str:
    """Render one resolved value for a table cell or a progress line.

    A missing field prints as an empty string rather than ``None`` so that a
    `-o jsonpath=` column reads the same as kubectl's.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)
