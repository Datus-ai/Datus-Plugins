"""`datus airflow dags ...` — DAG-level commands (list/trigger/pause/.../deploy)."""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict, List

from ..errors import ApiError, PluginError, UsageError
from ..output import render_one, render_rows
from . import (
    PROG,
    Context,
    add_output_option,
    confirm,
    parse_datetime_arg,
    parse_json_arg,
    quote_path_part,
)
from .deploy_cmd import register_deploy, register_undeploy

RUN_STATES = ("queued", "running", "success", "failed")
TERMINAL_RUN_STATES = {"success", "failed"}


def register(sub: argparse._SubParsersAction) -> None:
    dags = sub.add_parser("dags", help="manage DAGs (list, trigger, pause, deploy, ...)")
    group = dags.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list DAGs")
    p.add_argument("-p", "--pattern", help="filter dag_id with a SQL LIKE pattern (%% wildcard)")
    p.add_argument("-t", "--tag", action="append", dest="tags", help="filter by tag (repeatable)")
    p.add_argument("--owner", action="append", dest="owners", help="filter by owner (repeatable)")
    paused = p.add_mutually_exclusive_group()
    paused.add_argument("--paused", action="store_true", help="only paused DAGs")
    paused.add_argument("--unpaused", action="store_true", help="only unpaused DAGs")
    p.add_argument("--include-stale", action="store_true", help="include stale (deleted-file) DAGs")
    p.add_argument("--limit", type=int, help="stop after N DAGs (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("details", help="show full serialized details of one DAG")
    p.add_argument("dag_id")
    add_output_option(p)
    p.set_defaults(func=cmd_details)

    p = group.add_parser("list-runs", help="list DAG runs (all DAGs when dag_id is omitted)")
    p.add_argument("dag_id", nargs="?", default="~")
    p.add_argument("--state", action="append", choices=RUN_STATES, help="filter by state (repeatable)")
    p.add_argument("--logical-date-gte", metavar="ISO")
    p.add_argument("--logical-date-lte", metavar="ISO")
    p.add_argument("--order-by", default="-run_after", help="sort field (default: -run_after)")
    p.add_argument("--limit", type=int, help="stop after N runs (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list_runs)

    p = group.add_parser("list-import-errors", help="list DAG import errors")
    add_output_option(p)
    p.set_defaults(func=cmd_list_import_errors)

    p = group.add_parser("show", help="print the task dependency tree of a DAG")
    p.add_argument("dag_id")
    p.set_defaults(func=cmd_show)

    p = group.add_parser("source", help="print the source code of a DAG file")
    p.add_argument("dag_id")
    p.add_argument("--version-number", type=int, help="specific DAG version (default: latest)")
    p.set_defaults(func=cmd_source)

    p = group.add_parser("pause", help="pause one or more DAGs")
    p.add_argument("dag_id", nargs="+")
    p.set_defaults(func=cmd_pause, pause=True)

    p = group.add_parser("unpause", help="unpause one or more DAGs")
    p.add_argument("dag_id", nargs="+")
    p.set_defaults(func=cmd_pause, pause=False)

    p = group.add_parser("trigger", help="trigger a new DAG run")
    p.add_argument("dag_id")
    p.add_argument("-c", "--conf", help="JSON dict passed to the run as conf")
    p.add_argument("-r", "--run-id", help="custom run id (default: server-generated)")
    p.add_argument("-l", "--logical-date", metavar="ISO", help="logical date (ISO 8601 or 'now')")
    p.add_argument("--note", help="note attached to the run")
    p.add_argument("--wait", action="store_true", help="poll until the run reaches success/failed")
    p.add_argument("--interval", type=float, default=5.0, help="poll interval seconds (with --wait)")
    p.add_argument("--timeout", type=float, default=3600.0, help="max seconds to wait (with --wait)")
    add_output_option(p)
    p.set_defaults(func=cmd_trigger)

    p = group.add_parser("state", help="print the state of a DAG run")
    p.add_argument("dag_id")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_state)

    p = group.add_parser("clear-run", help="clear a DAG run (reset its task instances)")
    p.add_argument("dag_id")
    p.add_argument("run_id")
    p.add_argument("--only-failed", action="store_true", help="only clear failed task instances")
    p.add_argument("--dry-run", action="store_true", help="only show what would be cleared")
    p.add_argument("-y", "--yes", action="store_true", help="do not prompt for confirmation")
    add_output_option(p)
    p.set_defaults(func=cmd_clear_run)

    p = group.add_parser("delete", help="delete all metadata of a DAG")
    p.add_argument("dag_id")
    p.add_argument("-y", "--yes", action="store_true", help="do not prompt for confirmation")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("next-execution", help="show the next scheduled run of a DAG")
    p.add_argument("dag_id")
    p.set_defaults(func=cmd_next_execution)

    register_deploy(group)
    register_undeploy(group)


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    params: Dict[str, Any] = {
        "dag_id_pattern": ns.pattern,
        "tags": ns.tags,
        "owners": ns.owners,
    }
    if params["dag_id_pattern"] is None and len(ctx.settings.dag_id_prefix) == 1:
        # Narrows what the server sends back. dag_id_pattern is a substring
        # match on both API generations, so filter_dag_rows below is what
        # actually enforces the prefix.
        params["dag_id_pattern"] = ctx.settings.dag_id_prefix[0]
    if ns.paused:
        params["paused"] = "true"
    elif ns.unpaused:
        params["paused"] = "false"
    if ns.include_stale:
        params["exclude_stale"] = "false"
    rows = ctx.filter_dag_rows(ctx.client.paginate("/dags", "dags", params=params, limit=ns.limit))
    print(render_rows(rows, ["dag_id", "fileloc", "owners", "is_paused"], ns.output))
    return 0


def cmd_details(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    suffix = "" if ctx.client.is_v1 else "/details"
    data = ctx.client.request("GET", f"/dags/{quote_path_part(ns.dag_id)}{suffix}")
    print(render_one(data, ns.output))
    return 0


def cmd_list_runs(ctx: Context, ns) -> int:
    if ns.dag_id != "~":  # "~" means "across every DAG" — filtered after the call
        ctx.check_dag_id(ns.dag_id)
    order_by = ns.order_by
    if ctx.client.is_v1 and order_by == "-run_after":
        order_by = "-execution_date"
    params: Dict[str, Any] = {
        "state": ns.state,
        "order_by": order_by,
    }
    date_field = "execution_date" if ctx.client.is_v1 else "logical_date"
    if ns.logical_date_gte:
        params[f"{date_field}_gte"] = parse_datetime_arg(ns.logical_date_gte, "--logical-date-gte")
    if ns.logical_date_lte:
        params[f"{date_field}_lte"] = parse_datetime_arg(ns.logical_date_lte, "--logical-date-lte")
    rows = ctx.client.paginate(
        f"/dags/{quote_path_part(ns.dag_id)}/dagRuns", "dag_runs", params=params, limit=ns.limit
    )
    if ctx.client.is_v1:
        for row in rows:
            row.setdefault("logical_date", row.get("execution_date"))
    if ns.dag_id == "~":
        rows = ctx.filter_dag_rows(rows)
    columns = ["dag_id", "dag_run_id", "state", "run_type", "logical_date", "start_date", "end_date"]
    print(render_rows(rows, columns, ns.output))
    return 0


def cmd_list_import_errors(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/importErrors", "import_errors")
    for row in rows:
        stack = str(row.get("stack_trace", ""))
        row["error"] = stack.strip().splitlines()[-1] if stack.strip() else ""
    columns = ["filename", "bundle_name", "timestamp", "error"]
    print(render_rows(rows, columns, ns.output))
    if ctx.settings.scoped:
        # import errors are keyed by filename, which cannot be mapped to a
        # dag_id reliably (one file may declare several DAGs, or none)
        print(
            "note: import errors are not filtered by dag_id_prefix", file=sys.stderr
        )
    return 0


def cmd_show(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    data = ctx.client.request("GET", f"/dags/{quote_path_part(ns.dag_id)}/tasks")
    tasks: List[Dict[str, Any]] = data.get("tasks", [])
    by_id = {t["task_id"]: t for t in tasks}
    has_upstream = {
        downstream
        for t in tasks
        for downstream in (t.get("downstream_task_ids") or [])
    }
    roots = [t["task_id"] for t in tasks if t["task_id"] not in has_upstream]

    print(ns.dag_id)
    expanded: set[str] = set()

    def walk(task_id: str, prefix: str, is_last: bool) -> None:
        task = by_id.get(task_id, {})
        connector = "└── " if is_last else "├── "
        label = task_id
        operator = task.get("operator_name")
        if operator:
            label += f" [{operator}]"
        if task.get("is_mapped"):
            label += " (mapped)"
        children = list(task.get("downstream_task_ids") or [])
        if task_id in expanded and children:
            print(f"{prefix}{connector}{label} …")
            return
        expanded.add(task_id)
        print(f"{prefix}{connector}{label}")
        child_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(children):
            walk(child, child_prefix, i == len(children) - 1)

    for i, root in enumerate(roots):
        walk(root, "", i == len(roots) - 1)
    return 0


def cmd_source(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    if ctx.client.is_v1:
        dag = ctx.client.request("GET", f"/dags/{quote_path_part(ns.dag_id)}")
        file_token = (dag or {}).get("file_token")
        if not file_token:
            raise PluginError(f"Airflow v1 returned no file_token for DAG {ns.dag_id!r}")
        source_id = quote_path_part(str(file_token))
        params = None
    else:
        source_id = quote_path_part(ns.dag_id)
        params = {"version_number": ns.version_number} if ns.version_number else None
    text = ctx.client.request(
        "GET", f"/dagSources/{source_id}", params=params, accept="text/plain"
    )
    if isinstance(text, dict):  # server ignored the Accept header
        text = text.get("content", "")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def cmd_pause(ctx: Context, ns) -> int:
    ctx.check_dag_ids(ns.dag_id)  # all-or-nothing: never half-apply a bulk pause
    for dag_id in ns.dag_id:
        params = None if ctx.client.is_v1 else {"update_mask": "is_paused"}
        data = ctx.client.request(
            "PATCH",
            f"/dags/{quote_path_part(dag_id)}",
            params=params,
            json_body={"is_paused": ns.pause},
        )
        print(f"{data.get('dag_id', dag_id)}: is_paused={data.get('is_paused')}")
    return 0


def cmd_trigger(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    _reject_when_paused(ctx, ns.dag_id)
    body: Dict[str, Any] = {}
    if ns.logical_date:
        date_key = "execution_date" if ctx.client.is_v1 else "logical_date"
        body[date_key] = parse_datetime_arg(ns.logical_date, "--logical-date")
    elif not ctx.client.is_v1:
        # Required by API v2 but nullable; null lets the server assign it.
        body["logical_date"] = None
    if ns.run_id:
        body["dag_run_id"] = ns.run_id
    if ns.conf:
        conf = parse_json_arg(ns.conf, "--conf")
        if not isinstance(conf, dict):
            raise UsageError("--conf must be a JSON object")
        body["conf"] = conf
    if ns.note:
        body["note"] = ns.note

    run = ctx.client.request(
        "POST", f"/dags/{quote_path_part(ns.dag_id)}/dagRuns", json_body=body
    )
    print(render_one(run, ns.output))
    if not ns.wait:
        return 0
    return _wait_for_run(ctx, ns.dag_id, run["dag_run_id"], ns.interval, ns.timeout)


def _reject_when_paused(ctx: Context, dag_id: str) -> None:
    """Refuse to trigger a paused DAG — the scheduler never picks such a run up.

    Airflow happily creates the run and leaves it `queued` forever, so without
    this check `--wait` burns its whole timeout on a run that cannot start.
    Best-effort: a server that hides `GET /dags/{dag_id}` must not break
    trigger, so only a definite ``is_paused: true`` aborts.
    """
    try:
        dag = ctx.client.request("GET", f"/dags/{quote_path_part(dag_id)}")
    except ApiError:
        return
    if (dag or {}).get("is_paused"):
        raise UsageError(
            f"DAG {dag_id!r} is paused: the run would stay queued and never start — "
            f"run `{PROG} dags unpause {dag_id}` first"
        )


def _wait_for_run(ctx: Context, dag_id: str, run_id: str, interval: float, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    last_state = None
    path = f"/dags/{quote_path_part(dag_id)}/dagRuns/{quote_path_part(run_id)}"
    while True:
        state = ctx.client.request("GET", path).get("state")
        if state != last_state:
            # progress goes to stderr so `-o json` stdout stays machine-parseable
            print(f"run {run_id}: {state}", file=sys.stderr)
            last_state = state
        if state in TERMINAL_RUN_STATES:
            return 0 if state == "success" else 1
        if time.monotonic() >= deadline:
            raise PluginError(f"timed out after {timeout:.0f}s waiting for run {run_id} (state: {state})")
        time.sleep(interval)


def cmd_state(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    run = ctx.client.request(
        "GET",
        f"/dags/{quote_path_part(ns.dag_id)}/dagRuns/{quote_path_part(ns.run_id)}",
    )
    print(run.get("state"))
    return 0


def cmd_clear_run(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    if ctx.client.is_v1:
        path = f"/dags/{quote_path_part(ns.dag_id)}/clearTaskInstances"
        request_body = {
            "dag_run_id": ns.run_id,
            "dry_run": True,
            "only_failed": ns.only_failed,
            "only_running": False,
        }
    else:
        path = f"/dags/{quote_path_part(ns.dag_id)}/dagRuns/{quote_path_part(ns.run_id)}/clear"
        request_body = {"dry_run": True, "only_failed": ns.only_failed}
    preview = ctx.client.request("POST", path, json_body=request_body)
    instances = (preview or {}).get("task_instances", [])
    columns = ["task_id", "map_index", "state", "try_number"]
    print(render_rows(instances, columns, ns.output))
    if ns.dry_run:
        return 0
    if not instances:
        print("nothing to clear")
        return 0
    if not confirm(f"clear {len(instances)} task instance(s) of run {ns.run_id}?", ns.yes):
        print("aborted")
        return 1
    ctx.client.request("POST", path, json_body={**request_body, "dry_run": False})
    print(f"cleared {len(instances)} task instance(s)")
    return 0


def cmd_delete(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    if not confirm(
        f"delete DAG {ns.dag_id!r} and ALL its metadata (runs, task history)?", ns.yes
    ):
        print("aborted")
        return 1
    ctx.client.request("DELETE", f"/dags/{quote_path_part(ns.dag_id)}")
    print(f"deleted dag {ns.dag_id}")
    return 0


def cmd_next_execution(ctx: Context, ns) -> int:
    ctx.check_dag_id(ns.dag_id)
    dag = ctx.client.request("GET", f"/dags/{quote_path_part(ns.dag_id)}")
    logical = dag.get("next_dagrun_logical_date")
    run_after = dag.get("next_dagrun_run_after")
    if not logical and not run_after:
        print("None")
        if dag.get("is_paused"):
            print(f"note: dag {ns.dag_id} is paused", flush=True)
        return 0
    print(f"logical_date: {logical}")
    print(f"run_after:    {run_after}")
    return 0
