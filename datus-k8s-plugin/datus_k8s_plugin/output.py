"""kubectl-like common output formats."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from . import jsonpath
from .errors import UsageError

FORMATS = ("table", "wide", "json", "yaml", "name")

#: `-o jsonpath={.a.b}` prints one resolved value per object.
JSONPATH_PREFIX = "jsonpath="

#: Longest `status.error`-style text a wide table will carry inline. The full
#: value stays reachable with `-o jsonpath=`.
MESSAGE_WIDTH = 100


def output_format(value: str) -> str:
    """argparse ``type`` for ``-o``: one of :data:`FORMATS` or a jsonpath expression."""
    if value in FORMATS or value.startswith(JSONPATH_PREFIX):
        return value
    raise UsageError(
        f"unsupported output format {value!r}: expected one of {', '.join(FORMATS)}, "
        "or jsonpath={.path.to.field}"
    )


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


def _singular(kind: str | None) -> str:
    name = str(kind or "").strip()
    if name.endswith("s"):
        name = name[:-1]
    return name.lower()


def _truncate(text: str, width: int = MESSAGE_WIDTH) -> str:
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"


def _container_states(statuses: list[Any]) -> list[dict[str, Any]]:
    return [status for status in statuses if isinstance(status, dict)]


def _waiting_or_terminated_reason(status: dict[str, Any]) -> str:
    state = status.get("state") or {}
    waiting = state.get("waiting") or {}
    terminated = state.get("terminated") or {}
    return str(waiting.get("reason") or terminated.get("reason") or "")


def _init_status(statuses: list[dict[str, Any]]) -> str:
    """Summarise init containers the way kubectl does, or "" when they all passed.

    An init container stuck in `CrashLoopBackOff` is the single most common reason
    a pod sits at `Pending` forever, so it has to reach the STATUS column instead
    of being reachable only through the raw object.
    """
    completed = 0
    for status in statuses:
        state = status.get("state") or {}
        terminated = state.get("terminated") or {}
        if terminated and terminated.get("exitCode") in (0, "0"):
            completed += 1
            continue
        if terminated:
            reason = str(terminated.get("reason") or "")
            code = terminated.get("exitCode")
            return f"Init:{reason}" if reason else f"Init:ExitCode:{code}"
        reason = str((state.get("waiting") or {}).get("reason") or "")
        if reason and reason != "PodInitializing":
            return f"Init:{reason}"
        return f"Init:{completed}/{len(statuses)}"
    return ""


def _pod_row(meta: dict[str, Any], status: dict[str, Any]) -> list[str]:
    containers = _container_states(status.get("containerStatuses") or [])
    init_containers = _container_states(status.get("initContainerStatuses") or [])
    ready = sum(1 for item in containers if item.get("ready") is True)
    # Init restarts are counted too: the pod that stalled in this plugin's own
    # field testing was an init container restarting, which kubectl's
    # main-container-only count reports as 0.
    restarts = sum(int(item.get("restartCount") or 0) for item in containers + init_containers)
    state = (
        "Terminating"
        if meta.get("deletionTimestamp") or meta.get("deletion_timestamp")
        else _init_status(init_containers)
        or next((r for r in map(_waiting_or_terminated_reason, containers) if r), "")
        or str(status.get("reason") or status.get("phase") or "")
    )
    return [
        str(meta.get("name") or "<unknown>"),
        f"{ready}/{len(containers)}" if containers else "0/0",
        state,
        str(restarts),
        _age(meta.get("creationTimestamp") or meta.get("creation_timestamp")),
    ]


def _event_row(row: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    target = row.get("involvedObject") or row.get("involved_object") or {}
    kind = _singular(target.get("kind"))
    name = target.get("name") or ""
    return [
        _age(
            row.get("lastTimestamp")
            or row.get("last_timestamp")
            or row.get("eventTime")
            or row.get("event_time")
            or meta.get("creationTimestamp")
        ),
        str(row.get("type") or ""),
        str(row.get("reason") or ""),
        f"{kind}/{name}" if kind and name else str(name),
        " ".join(str(row.get("message") or "").split()),
    ]


def _generic_state(status: dict[str, Any]) -> str:
    """Best-effort STATUS for a resource that is neither a pod nor an event.

    Custom resources rarely populate `status.phase` or `status.conditions`; they
    invent their own field. Probing a fixed, documented list of the conventional
    names keeps a FlinkDeployment-shaped object from rendering a blank column
    without guessing at arbitrary keys.
    """
    for key in ("phase", "reason", "state", "lifecycleState", "lifecycle_state", "health"):
        value = status.get(key)
        if isinstance(value, str) and value:
            return value
    conditions = status.get("conditions") or []
    ready = next(
        (c for c in conditions if isinstance(c, dict) and c.get("type") in {"Ready", "Available"}),
        None,
    )
    return str(ready.get("status") or "") if ready else ""


def _generic_message(status: dict[str, Any]) -> str:
    """The failure text a resource is carrying, if any."""
    error = status.get("error")
    if isinstance(error, str) and error:
        return _truncate(error)
    if isinstance(error, dict) and error:
        return _truncate(json.dumps(error, ensure_ascii=False, default=str))
    for condition in status.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if str(condition.get("status")) == "False" and condition.get("message"):
            return _truncate(str(condition["message"]))
    return ""


def _table(
    rows: list[dict[str, Any]], fmt: str, kind: str | None
) -> tuple[list[str], list[list[str]]]:
    singular = _singular(kind)
    per_kind = {"pod", "event"}
    if singular not in per_kind:
        # Trust the objects themselves when the caller's hint is a plural the API
        # server resolved to something else.
        singular = _singular(next((r.get("kind") for r in rows if r.get("kind")), None)) or singular

    if singular == "pod":
        headers = ["NAME", "READY", "STATUS", "RESTARTS", "AGE"]
        if fmt == "wide":
            headers.extend(["NAMESPACE", "IP", "NODE"])
    elif singular == "event":
        headers = ["LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"]
        if fmt == "wide":
            headers.insert(0, "NAMESPACE")
            headers.insert(1, "COUNT")
    else:
        headers = ["NAME", "STATUS", "AGE"]
        if fmt == "wide":
            headers.extend(["NAMESPACE", "API VERSION", "KIND", "MESSAGE"])

    table: list[list[str]] = []
    for row in rows:
        meta = row.get("metadata") or {}
        status = row.get("status") or {}
        namespace = str(meta.get("namespace") or "")
        if singular == "pod":
            cells = _pod_row(meta, status)
            if fmt == "wide":
                spec = row.get("spec") or {}
                cells.extend(
                    [
                        namespace,
                        str(status.get("podIP") or status.get("pod_ip") or ""),
                        str(spec.get("nodeName") or spec.get("node_name") or ""),
                    ]
                )
        elif singular == "event":
            cells = _event_row(row, meta)
            if fmt == "wide":
                cells.insert(0, namespace)
                cells.insert(1, str(row.get("count") or ""))
        else:
            cells = [
                str(meta.get("name") or "<unknown>"),
                _generic_state(status),
                _age(meta.get("creationTimestamp") or meta.get("creation_timestamp")),
            ]
            if fmt == "wide":
                cells.extend(
                    [
                        namespace,
                        str(row.get("apiVersion") or row.get("api_version") or ""),
                        str(row.get("kind") or kind or ""),
                        _generic_message(status),
                    ]
                )
        table.append(cells)
    return headers, table


def render(value: Any, fmt: str = "table", kind: str | None = None) -> str:
    data = plain(value)
    rows = objects(data)
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if fmt == "yaml":
        import yaml

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip()
    if fmt.startswith(JSONPATH_PREFIX):
        expression = fmt[len(JSONPATH_PREFIX) :]
        return "\n".join(jsonpath.display(jsonpath.resolve(row, expression)) for row in rows)
    if fmt == "name":
        lines = []
        for row in rows:
            meta = row.get("metadata") or {}
            item_kind = str(row.get("kind") or kind or "resource").lower()
            lines.append(f"{item_kind}/{meta.get('name', '<unknown>')}")
        return "\n".join(lines)

    headers, table = _table(rows, fmt, kind)
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
