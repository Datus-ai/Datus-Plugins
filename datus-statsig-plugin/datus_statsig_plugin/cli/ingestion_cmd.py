"""`datus statsig ingestion ...` — ETL ingestion runs, status, backfills, schedule."""

from __future__ import annotations

import argparse

from ..output import render, render_one, render_rows
from . import Context, add_output_option, quote_path_part, resolve_fmt

RUN_COLUMNS = ["runID", "latestStatus", "lastUpdatedAt", "trigger"]
STATUS_COLUMNS = ["timestamp", "ingestion_dataset", "ingestion_source", "status"]


def register(sub: argparse._SubParsersAction) -> None:
    ing = sub.add_parser(
        "ingestion", help="ETL ingestion: get/runs/run/status/schedule-get + backfill/schedule-set"
    )
    group = ing.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("get", help="read an ingestion source config")
    p.add_argument("--type", required=True, help="dataset type (e.g. metrics, events)")
    p.add_argument("--dataset", required=True, help="dataset")
    p.add_argument("--source-name", help="source name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("runs", help="list ingestion runs")
    p.add_argument("--limit", type=int, help="stop after N runs (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_runs)

    p = group.add_parser("run", help="read a single ingestion run by id")
    p.add_argument("id")
    add_output_option(p)
    p.set_defaults(func=cmd_run)

    p = group.add_parser("status", help="list ingestion status entries over a window")
    p.add_argument("--start", required=True, help="start date (YYYY-MM-DD)")
    p.add_argument("--end", required=True, help="end date (YYYY-MM-DD)")
    p.add_argument("--source", help="filter by source")
    p.add_argument("--dataset", help="filter by dataset")
    p.add_argument("--status", help="filter by status")
    p.add_argument("--limit", type=int, help="stop after N entries (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_status)

    p = group.add_parser("backfill", help="kick off a backfill run over a date range (confirms)")
    p.add_argument("--type", required=True, help="dataset type")
    p.add_argument("--dataset", required=True, help="dataset")
    p.add_argument("--start", required=True, help="datestamp_start (YYYY-MM-DD)")
    p.add_argument("--end", required=True, help="datestamp_end (YYYY-MM-DD)")
    p.add_argument("--source", help="source name")
    add_output_option(p)
    p.set_defaults(func=cmd_backfill)

    p = group.add_parser("schedule-get", help="read the ingestion schedule for a dataset")
    p.add_argument("--dataset", required=True)
    add_output_option(p)
    p.set_defaults(func=cmd_schedule_get)

    p = group.add_parser("schedule-set", help="set the ingestion schedule for a dataset (confirms)")
    p.add_argument("--dataset", required=True)
    p.add_argument("--hour", type=int, required=True, help="scheduled_hour_pst (0-23)")
    add_output_option(p)
    p.set_defaults(func=cmd_schedule_set)


# ---------------------------------------------------------------- handlers


def cmd_get(ctx: Context, ns) -> int:
    params = {"type": ns.type, "dataset": ns.dataset, "source_name": ns.source_name}
    resp = ctx.client.request("GET", "/ingestion", params=params)
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_runs(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/ingestion/runs", limit=ns.limit)
    print(render_rows(rows, RUN_COLUMNS, resolve_fmt(ns)))
    return 0


def cmd_run(ctx: Context, ns) -> int:
    resp = ctx.client.request("GET", f"/ingestion/runs/{quote_path_part(ns.id)}")
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_status(ctx: Context, ns) -> int:
    params = {
        "startDate": ns.start,
        "endDate": ns.end,
        "source": ns.source,
        "dataset": ns.dataset,
        "status": ns.status,
    }
    rows = ctx.client.paginate("/ingestion/status", params=params, limit=ns.limit)
    print(render_rows(rows, STATUS_COLUMNS, resolve_fmt(ns)))
    return 0


def cmd_backfill(ctx: Context, ns) -> int:
    body = {
        "datestamp_start": ns.start,
        "datestamp_end": ns.end,
        "type": ns.type,
        "dataset": ns.dataset,
    }
    if ns.source:
        body["source"] = ns.source
    resp = ctx.client.request("POST", "/ingestion/backfill", json_body=body)
    print(render((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_schedule_get(ctx: Context, ns) -> int:
    resp = ctx.client.request("GET", "/ingestion/schedule", params={"dataset": ns.dataset})
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_schedule_set(ctx: Context, ns) -> int:
    body = {"dataset": ns.dataset, "scheduled_hour_pst": ns.hour}
    resp = ctx.client.request("POST", "/ingestion/schedule", json_body=body)
    print(render((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0
