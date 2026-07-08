"""`datus statsig logs ...` — Logs Explorer query (events / logs / spans, read-only)."""

from __future__ import annotations

import argparse

from ..output import render
from . import Context, add_output_option, resolve_fmt


def register(sub: argparse._SubParsersAction) -> None:
    logs = sub.add_parser("logs", help="query the Logs Explorer")
    group = logs.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("query", help="query recent events / logs / spans")
    p.add_argument("--query", help="Logs Explorer filter expression")
    p.add_argument("--source", choices=("logs", "events", "spans"), help="source (default: logs)")
    p.add_argument("--columns", help="comma-separated column ids to include")
    p.add_argument("--start", type=int, dest="start_ts", help="start_ts (Unix ms)")
    p.add_argument("--end", type=int, dest="end_ts", help="end_ts (Unix ms)")
    p.add_argument("--limit", type=int, help="max rows (default 100, max 1000)")
    p.add_argument("--after", help="opaque pagination cursor")
    add_output_option(p)
    p.set_defaults(func=cmd_query)


def cmd_query(ctx: Context, ns) -> int:
    params = {
        "query": ns.query,
        "source": ns.source,
        "columns": ns.columns,
        "start_ts": ns.start_ts,
        "end_ts": ns.end_ts,
        "limit": ns.limit,
        "after": ns.after,
    }
    # Cursor-paginated, not page-based — return the single page as the API gives it.
    resp = ctx.client.request("GET", "/logs", params=params)
    print(render(resp, resolve_fmt(ns)))
    return 0
