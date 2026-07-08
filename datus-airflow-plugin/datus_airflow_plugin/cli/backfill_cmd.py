"""`datus airflow backfill ...` — manage backfills (Airflow 3 top-level group)."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from ..errors import UsageError
from ..output import render_one, render_rows
from . import Context, add_output_option, parse_datetime_arg, parse_json_arg

BACKFILL_COLUMNS = [
    "id",
    "dag_id",
    "from_date",
    "to_date",
    "is_paused",
    "reprocess_behavior",
    "max_active_runs",
    "completed_at",
]


def register(sub: argparse._SubParsersAction) -> None:
    backfill = sub.add_parser("backfill", help="create and control backfills")
    group = backfill.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("create", help="start a backfill for a date range")
    p.add_argument("--dag-id", required=True)
    p.add_argument("--from-date", required=True, metavar="ISO")
    p.add_argument("--to-date", required=True, metavar="ISO")
    p.add_argument("--run-backwards", action="store_true", help="newest logical date first")
    p.add_argument("--dag-run-conf", help="JSON dict passed to every backfill run")
    p.add_argument(
        "--reprocess-behavior",
        choices=("none", "failed", "completed"),
        default="none",
        help="what to do with logical dates that already have runs (default: none)",
    )
    p.add_argument("--max-active-runs", type=int, default=10)
    p.add_argument("--dry-run", action="store_true", help="only list the logical dates a backfill would create")
    add_output_option(p)
    p.set_defaults(func=cmd_create)

    p = group.add_parser("list", help="list backfills of a DAG")
    p.add_argument("--dag-id", required=True)
    p.add_argument("--limit", type=int, help="stop after N backfills (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    for name, help_text in (
        ("pause", "pause a running backfill"),
        ("unpause", "resume a paused backfill"),
        ("cancel", "cancel a backfill"),
    ):
        p = group.add_parser(name, help=help_text)
        p.add_argument("backfill_id", type=int)
        add_output_option(p)
        p.set_defaults(func=cmd_update_state, action=name)


def _body_from_args(ns) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "dag_id": ns.dag_id,
        "from_date": parse_datetime_arg(ns.from_date, "--from-date"),
        "to_date": parse_datetime_arg(ns.to_date, "--to-date"),
        "run_backwards": ns.run_backwards,
        "reprocess_behavior": ns.reprocess_behavior,
        "max_active_runs": ns.max_active_runs,
    }
    if ns.dag_run_conf:
        conf = parse_json_arg(ns.dag_run_conf, "--dag-run-conf")
        if not isinstance(conf, dict):
            raise UsageError("--dag-run-conf must be a JSON object")
        body["dag_run_conf"] = conf
    return body


# ---------------------------------------------------------------- handlers


def cmd_create(ctx: Context, ns) -> int:
    body = _body_from_args(ns)
    if ns.dry_run:
        data = ctx.client.request("POST", "/backfills/dry_run", json_body=body)
        rows = (data or {}).get("backfills", [])
        print(render_rows(rows, ["logical_date"], ns.output))
        print(f"[dry-run] backfill would create {len(rows)} run(s)")
        return 0
    data = ctx.client.request("POST", "/backfills", json_body=body)
    print(render_one(data, ns.output))
    return 0


def cmd_list(ctx: Context, ns) -> int:
    rows = ctx.client.paginate(
        "/backfills", "backfills", params={"dag_id": ns.dag_id}, limit=ns.limit
    )
    print(render_rows(rows, BACKFILL_COLUMNS, ns.output))
    return 0


def cmd_update_state(ctx: Context, ns) -> int:
    data = ctx.client.request("PUT", f"/backfills/{ns.backfill_id}/{ns.action}")
    print(render_one(data, ns.output))
    return 0
