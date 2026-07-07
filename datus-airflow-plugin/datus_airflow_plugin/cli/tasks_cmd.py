"""`datus airflow tasks ...` — task and task-instance commands."""

from __future__ import annotations

import argparse
import re
from typing import Any, Dict, List

from ..errors import UsageError
from ..output import render_rows
from . import Context, add_output_option, confirm, parse_datetime_arg, quote_path_part

TI_COLUMNS = ["task_id", "map_index", "state", "start_date", "end_date", "try_number"]


def register(sub: argparse._SubParsersAction) -> None:
    tasks = sub.add_parser("tasks", help="manage tasks and task instances")
    group = tasks.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list the tasks of a DAG")
    p.add_argument("dag_id")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("state", help="print the state of a task instance")
    p.add_argument("dag_id")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("--map-index", type=int, default=-1, help="map index for mapped tasks")
    p.set_defaults(func=cmd_state)

    p = group.add_parser("states-for-dag-run", help="list all task instances of a DAG run")
    p.add_argument("dag_id")
    p.add_argument("run_id")
    add_output_option(p)
    p.set_defaults(func=cmd_states_for_dag_run)

    p = group.add_parser("clear", help="clear task instances so they re-run")
    p.add_argument("dag_id")
    p.add_argument("-t", "--task-regex", help="only tasks whose task_id matches this regex")
    p.add_argument("-r", "--run-id", help="only within this DAG run")
    p.add_argument("-s", "--start-date", metavar="ISO", help="only runs at/after this date")
    p.add_argument("-e", "--end-date", metavar="ISO", help="only runs at/before this date")
    p.add_argument("--only-failed", action="store_true", help="only failed task instances")
    p.add_argument("--only-running", action="store_true", help="only running task instances")
    p.add_argument("-u", "--upstream", action="store_true", help="include upstream tasks")
    p.add_argument("-d", "--downstream", action="store_true", help="include downstream tasks")
    p.add_argument("--future", action="store_true", help="include later runs")
    p.add_argument("--past", action="store_true", help="include earlier runs")
    p.add_argument("--dry-run", action="store_true", help="only show what would be cleared")
    p.add_argument("-y", "--yes", action="store_true", help="do not prompt for confirmation")
    add_output_option(p)
    p.set_defaults(func=cmd_clear)

    p = group.add_parser("failed-deps", help="show what blocks a task instance from being scheduled")
    p.add_argument("dag_id")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("--map-index", type=int, help="map index for mapped tasks")
    add_output_option(p)
    p.set_defaults(func=cmd_failed_deps)

    p = group.add_parser("logs", help="print the log of a task instance try")
    p.add_argument("dag_id")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("try_number", nargs="?", type=int, help="default: the latest try")
    p.add_argument("--map-index", type=int, default=-1, help="map index for mapped tasks")
    p.add_argument("--full-content", action="store_true", help="fetch the full log, not just the tail")
    p.set_defaults(func=cmd_logs)


def _ti_path(dag_id: str, run_id: str, task_id: str, map_index: int | None = None) -> str:
    path = (
        f"/dags/{quote_path_part(dag_id)}/dagRuns/{quote_path_part(run_id)}"
        f"/taskInstances/{quote_path_part(task_id)}"
    )
    if map_index is not None and map_index >= 0:
        path += f"/{map_index}"
    return path


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    data = ctx.client.request("GET", f"/dags/{quote_path_part(ns.dag_id)}/tasks")
    rows = data.get("tasks", [])
    columns = ["task_id", "operator_name", "trigger_rule", "retries", "downstream_task_ids"]
    print(render_rows(rows, columns, ns.output))
    return 0


def cmd_state(ctx: Context, ns) -> int:
    ti = ctx.client.request("GET", _ti_path(ns.dag_id, ns.run_id, ns.task_id, ns.map_index))
    print(ti.get("state"))
    return 0


def cmd_states_for_dag_run(ctx: Context, ns) -> int:
    rows = ctx.client.paginate(
        f"/dags/{quote_path_part(ns.dag_id)}/dagRuns/{quote_path_part(ns.run_id)}/taskInstances",
        "task_instances",
    )
    print(render_rows(rows, TI_COLUMNS, ns.output))
    return 0


def cmd_clear(ctx: Context, ns) -> int:
    body: Dict[str, Any] = {
        # the API defaults only_failed to true; send both flags explicitly so the
        # CLI behaves like `airflow tasks clear` (clear everything unless narrowed)
        "only_failed": ns.only_failed,
        "only_running": ns.only_running,
        "include_upstream": ns.upstream,
        "include_downstream": ns.downstream,
        "include_future": ns.future,
        "include_past": ns.past,
    }
    if ns.run_id:
        body["dag_run_id"] = ns.run_id
    if ns.start_date:
        body["start_date"] = parse_datetime_arg(ns.start_date, "--start-date")
    if ns.end_date:
        body["end_date"] = parse_datetime_arg(ns.end_date, "--end-date")
    if ns.task_regex:
        body["task_ids"] = _matching_task_ids(ctx, ns.dag_id, ns.task_regex)

    path = f"/dags/{quote_path_part(ns.dag_id)}/clearTaskInstances"
    preview = ctx.client.request("POST", path, json_body={**body, "dry_run": True})
    instances: List[Dict[str, Any]] = (preview or {}).get("task_instances", [])
    print(render_rows(instances, ["dag_run_id"] + TI_COLUMNS, ns.output))
    if ns.dry_run:
        return 0
    if not instances:
        print("nothing to clear")
        return 0
    if not confirm(f"clear {len(instances)} task instance(s)?", ns.yes):
        print("aborted")
        return 1
    ctx.client.request("POST", path, json_body={**body, "dry_run": False})
    print(f"cleared {len(instances)} task instance(s)")
    return 0


def _matching_task_ids(ctx: Context, dag_id: str, pattern: str) -> List[str]:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise UsageError(f"invalid --task-regex: {exc}") from exc
    data = ctx.client.request("GET", f"/dags/{quote_path_part(dag_id)}/tasks")
    matched = [t["task_id"] for t in data.get("tasks", []) if regex.search(t["task_id"])]
    if not matched:
        raise UsageError(f"no task of dag {dag_id!r} matches regex {pattern!r}")
    return matched


def cmd_failed_deps(ctx: Context, ns) -> int:
    path = _ti_path(ns.dag_id, ns.run_id, ns.task_id, ns.map_index) + "/dependencies"
    data = ctx.client.request("GET", path)
    deps = (data or {}).get("dependencies", [])
    if not deps:
        print("no blocking dependencies")
        return 0
    print(render_rows(deps, ["name", "reason"], ns.output))
    return 0


def cmd_logs(ctx: Context, ns) -> int:
    try_number = ns.try_number
    if try_number is None:
        ti = ctx.client.request("GET", _ti_path(ns.dag_id, ns.run_id, ns.task_id, ns.map_index))
        try_number = ti.get("try_number") or 1
    path = _ti_path(ns.dag_id, ns.run_id, ns.task_id) + f"/logs/{try_number}"
    data = ctx.client.request(
        "GET",
        path,
        params={
            "map_index": ns.map_index if ns.map_index >= 0 else None,
            "full_content": "true" if ns.full_content else None,
        },
        accept="application/json",
    )
    content = (data or {}).get("content") or []
    if isinstance(content, str):
        print(content)
        return 0
    for event in content:
        if not isinstance(event, dict):
            print(event)
            continue
        timestamp = event.get("timestamp") or ""
        message = event.get("event", "")
        extras = " ".join(
            f"{k}={v}" for k, v in event.items() if k not in ("timestamp", "event")
        )
        line = " ".join(part for part in (f"[{timestamp}]" if timestamp else "", message, extras) if part)
        print(line)
    return 0
