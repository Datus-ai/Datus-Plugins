"""`datus statsig events ...` — recently logged events (read-only)."""

from __future__ import annotations

import argparse

from ..output import render_rows
from . import Context, add_output_option, quote_path_part, resolve_fmt

COLUMNS = ["timestamp", "name", "userID", "value"]


def register(sub: argparse._SubParsersAction) -> None:
    events = sub.add_parser("events", help="recently logged events: list / get by name")
    group = events.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list recently logged events (rolling window)")
    p.add_argument("--limit", type=int, help="stop after N events (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="recently logged events for one event name")
    p.add_argument("event_name")
    p.add_argument("--limit", type=int, help="stop after N events (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_get)


def cmd_list(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/events", limit=ns.limit)
    print(render_rows(rows, COLUMNS, resolve_fmt(ns)))
    return 0


def cmd_get(ctx: Context, ns) -> int:
    rows = ctx.client.paginate(f"/events/{quote_path_part(ns.event_name)}", limit=ns.limit)
    print(render_rows(rows, COLUMNS, resolve_fmt(ns)))
    return 0
