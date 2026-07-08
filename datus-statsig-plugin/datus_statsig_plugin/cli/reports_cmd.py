"""`datus statsig reports ...` — generate an analytics report and return its URL."""

from __future__ import annotations

import argparse

from ..output import render
from . import Context, add_output_option, resolve_fmt

REPORT_TYPES = ("first_exposures", "pulse_daily", "topline_impact_daily")


def register(sub: argparse._SubParsersAction) -> None:
    reports = sub.add_parser("reports", help="generate an analytics report (returns a URL)")
    group = reports.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("get", help="get a report URL for a type + date")
    p.add_argument("--type", required=True, choices=REPORT_TYPES, help="report type")
    p.add_argument("--date", required=True, help="report date, e.g. 2024-09-01")
    add_output_option(p)
    p.set_defaults(func=cmd_get)


def cmd_get(ctx: Context, ns) -> int:
    resp = ctx.client.request("GET", "/reports", params={"type": ns.type, "date": ns.date})
    print(render((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0
