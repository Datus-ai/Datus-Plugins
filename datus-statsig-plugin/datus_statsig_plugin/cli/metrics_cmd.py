"""`datus statsig metrics ...` — metric definitions, values, generated SQL, authoring."""

from __future__ import annotations

import argparse

from ..output import render, render_one, render_rows
from ..schemas import epilog_for
from . import (
    Context,
    add_output_option,
    load_body,
    quote_path_part,
    resolve_fmt,
)

COLUMNS = ["id", "name", "type", "isVerified", "tags"]


def register(sub: argparse._SubParsersAction) -> None:
    metrics = sub.add_parser("metrics", help="metrics: list/get/sql/values + create/update/reload")
    group = metrics.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list metrics in the project")
    p.add_argument("-t", "--tag", action="append", dest="tags", help="filter by tag (repeatable)")
    p.add_argument("--show-hidden", action="store_true", help="include hidden metrics")
    p.add_argument("--limit", type=int, help="stop after N metrics (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="read a metric definition by id, or by --name + --type")
    p.add_argument("id", nargs="?", help="metric id")
    p.add_argument("--name", help="metric name (with --type, instead of id)")
    p.add_argument("--type", help="metric type (with --name)")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("sql", help="get the SQL generated for a metric")
    p.add_argument("id", help="metric id")
    add_output_option(p)
    p.set_defaults(func=cmd_sql)

    p = group.add_parser("values", help="list metric values for a date")
    p.add_argument("--date", required=True, help="date, e.g. 2024-09-01")
    p.add_argument("--metric-name", help="filter by metric name")
    p.add_argument("--metric-type", help="filter by metric type")
    p.add_argument("--limit", type=int, help="stop after N values (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_values)

    p = group.add_parser(
        "create",
        help="create a metric (analysis authoring — confirms)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_for("metrics create"),
    )
    _add_body_args(p, dry_run=True)
    add_output_option(p)
    p.set_defaults(func=cmd_create)

    p = group.add_parser(
        "update",
        help="update a metric (confirms)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_for("metrics update"),
    )
    p.add_argument("id", help="metric id")
    _add_body_args(p, dry_run=True)
    add_output_option(p)
    p.set_defaults(func=cmd_update)

    p = group.add_parser("reload", help="trigger a warehouse-native metric recompute (confirms)")
    p.add_argument("id", help="metric id")
    p.add_argument("--incremental", action="store_true", help="incremental reload")
    add_output_option(p)
    p.set_defaults(func=cmd_reload)


def _add_body_args(p: argparse.ArgumentParser, dry_run: bool = False) -> None:
    src = p.add_mutually_exclusive_group()
    src.add_argument("--json", help="request body as an inline JSON object")
    src.add_argument("--json-file", dest="json_file", help="request body from a JSON file")
    if dry_run:
        p.add_argument("--dry-run", action="store_true", help="validate the body without persisting")


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    params = {"tags": ns.tags}
    if ns.show_hidden:
        params["showHiddenMetrics"] = "true"
    rows = ctx.client.paginate("/metrics/list", params=params, limit=ns.limit)
    print(render_rows(rows, COLUMNS, resolve_fmt(ns)))
    return 0


def cmd_get(ctx: Context, ns) -> int:
    from ..errors import UsageError

    if ns.id:
        resp = ctx.client.request("GET", f"/metrics/{quote_path_part(ns.id)}")
    elif ns.name and ns.type:
        resp = ctx.client.request(
            "GET", f"/metrics/{quote_path_part(ns.name)}/{quote_path_part(ns.type)}"
        )
    else:
        raise UsageError("provide a metric id, or both --name and --type")
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_sql(ctx: Context, ns) -> int:
    resp = ctx.client.request("GET", f"/metrics/{quote_path_part(ns.id)}/sql")
    print(render((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_values(ctx: Context, ns) -> int:
    params = {"date": ns.date, "metricName": ns.metric_name, "metricType": ns.metric_type}
    rows = ctx.client.paginate("/metrics/values", params=params, limit=ns.limit)
    print(render_rows(rows, None, resolve_fmt(ns)))
    return 0


def cmd_create(ctx: Context, ns) -> int:
    body = load_body(ns, "metrics create")
    resp = ctx.client.request("POST", "/metrics", json_body=body)
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_update(ctx: Context, ns) -> int:
    body = load_body(ns, "metrics update")
    resp = ctx.client.request("POST", f"/metrics/{quote_path_part(ns.id)}", json_body=body)
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_reload(ctx: Context, ns) -> int:
    params = {"incremental": "true"} if ns.incremental else None
    resp = ctx.client.request("POST", f"/metrics/{quote_path_part(ns.id)}/reload", params=params)
    print(render(resp, resolve_fmt(ns)))
    return 0
