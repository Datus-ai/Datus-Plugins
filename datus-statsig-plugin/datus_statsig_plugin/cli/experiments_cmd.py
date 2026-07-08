"""`datus statsig experiments ...` — experiment metadata + Pulse / exposure readouts."""

from __future__ import annotations

import argparse

from ..output import render, render_one, render_rows
from . import Context, add_output_option, quote_path_part, resolve_fmt

COLUMNS = ["id", "name", "status"]


def register(sub: argparse._SubParsersAction) -> None:
    exp = sub.add_parser(
        "experiments", help="experiments: list/get + pulse/summary/exposures readouts, load-pulse"
    )
    group = exp.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list experiments")
    p.add_argument("--status", help="filter by status")
    p.add_argument("-t", "--tag", action="append", dest="tags", help="filter by tag (repeatable)")
    p.add_argument("--limit", type=int, help="stop after N experiments (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="read a single experiment")
    p.add_argument("id")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("pulse", help="full Pulse results (lift, CIs, p-values)")
    p.add_argument("id")
    p.add_argument("--control", required=True, help="control group id")
    p.add_argument("--test", required=True, help="test group id")
    p.add_argument("--cuped", help="CUPED setting")
    p.add_argument("--confidence", help="confidence interval level")
    p.add_argument("--date", help="results as of this date")
    add_output_option(p)
    p.set_defaults(func=cmd_pulse)

    p = group.add_parser("summary", help="summary charts / readout sections")
    p.add_argument("id")
    p.add_argument("--control", help="control group id")
    p.add_argument("--test", help="test group id")
    add_output_option(p)
    p.set_defaults(func=cmd_summary)

    p = group.add_parser("exposures", help="cumulative exposures (or --dimensional for SRM breakdown)")
    p.add_argument("id")
    p.add_argument("--dimensional", action="store_true", help="dimensional exposures / SRM")
    p.add_argument("--dimension-type", help="dimension type (with --dimensional)")
    p.add_argument("--severity", help="severity filter (with --dimensional)")
    add_output_option(p)
    p.set_defaults(func=cmd_exposures)

    p = group.add_parser("load-pulse", help="trigger a warehouse-native Pulse computation (confirms)")
    p.add_argument("id")
    p.add_argument("--refresh", action="store_true", help="force a refresh")
    add_output_option(p)
    p.set_defaults(func=cmd_load_pulse)

    p = group.add_parser("pulse-status", help="Pulse load history (optionally one dag id)")
    p.add_argument("id")
    p.add_argument("dag_id", nargs="?", help="specific pulse-load dag id")
    p.add_argument("--limit", type=int, help="stop after N history entries")
    add_output_option(p)
    p.set_defaults(func=cmd_pulse_status)


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    params = {"status": ns.status, "tags": ns.tags}
    rows = ctx.client.paginate("/experiments", params=params, limit=ns.limit)
    print(render_rows(rows, COLUMNS, resolve_fmt(ns)))
    return 0


def cmd_get(ctx: Context, ns) -> int:
    resp = ctx.client.request("GET", f"/experiments/{quote_path_part(ns.id)}")
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_pulse(ctx: Context, ns) -> int:
    params = {
        "control": ns.control,
        "test": ns.test,
        "cuped": ns.cuped,
        "confidence": ns.confidence,
        "date": ns.date,
    }
    resp = ctx.client.request(
        "GET", f"/experiments/{quote_path_part(ns.id)}/pulse_results", params=params
    )
    print(render((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_summary(ctx: Context, ns) -> int:
    params = {"control": ns.control, "test": ns.test}
    resp = ctx.client.request(
        "GET", f"/experiments/{quote_path_part(ns.id)}/summary_charts", params=params
    )
    print(render((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_exposures(ctx: Context, ns) -> int:
    if ns.dimensional:
        path = f"/experiments/{quote_path_part(ns.id)}/dimensional_exposures"
        params = {"dimension_type": ns.dimension_type, "severity": ns.severity}
    else:
        path = f"/experiments/{quote_path_part(ns.id)}/cumulative_exposures"
        params = None
    resp = ctx.client.request("GET", path, params=params)
    print(render((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_load_pulse(ctx: Context, ns) -> int:
    params = {"refresh": "true"} if ns.refresh else None
    resp = ctx.client.request(
        "POST", f"/experiments/{quote_path_part(ns.id)}/load_pulse", params=params
    )
    print(render(resp, resolve_fmt(ns)))
    return 0


def cmd_pulse_status(ctx: Context, ns) -> int:
    if ns.dag_id:
        resp = ctx.client.request(
            "GET",
            f"/experiments/{quote_path_part(ns.id)}/pulse_load_history/{quote_path_part(ns.dag_id)}",
        )
        print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
        return 0
    rows = ctx.client.paginate(
        f"/experiments/{quote_path_part(ns.id)}/pulse_load_history", limit=ns.limit
    )
    print(render_rows(rows, None, resolve_fmt(ns)))
    return 0
