"""`datus statsig metric-source ...` — warehouse-native metric sources (SQL authoring)."""

from __future__ import annotations

import argparse

from ..output import render, render_one, render_rows
from ..schemas import epilog_for
from . import Context, add_output_option, load_body, quote_path_part, resolve_fmt

COLUMNS = ["name", "description", "tags"]


def register(sub: argparse._SubParsersAction) -> None:
    ms = sub.add_parser(
        "metric-source", help="warehouse-native metric sources: list/get + create/update/delete"
    )
    group = ms.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list metric sources")
    p.add_argument("--limit", type=int, help="stop after N sources (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="read a metric source by name")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser(
        "create",
        help="create a metric source from SQL (confirms)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_for("metric-source create"),
    )
    _add_body_args(p, dry_run=True)
    add_output_option(p)
    p.set_defaults(func=cmd_create)

    p = group.add_parser(
        "update",
        help="update a metric source (confirms)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_for("metric-source update"),
    )
    p.add_argument("name")
    _add_body_args(p, dry_run=True)
    add_output_option(p)
    p.set_defaults(func=cmd_update)

    p = group.add_parser("delete", help="delete a metric source (confirms)")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_delete)


def _add_body_args(p: argparse.ArgumentParser, dry_run: bool = False) -> None:
    src = p.add_mutually_exclusive_group()
    src.add_argument("--json", help="request body as an inline JSON object")
    src.add_argument("--json-file", dest="json_file", help="request body from a JSON file")
    if dry_run:
        p.add_argument("--dry-run", action="store_true", help="validate the body without persisting")


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/metrics/metric_source/list", limit=ns.limit)
    print(render_rows(rows, COLUMNS, resolve_fmt(ns)))
    return 0


def cmd_get(ctx: Context, ns) -> int:
    resp = ctx.client.request("GET", f"/metrics/metric_source/{quote_path_part(ns.name)}")
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_create(ctx: Context, ns) -> int:
    body = load_body(ns, "metric-source create")
    resp = ctx.client.request("POST", "/metrics/metric_source", json_body=body)
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_update(ctx: Context, ns) -> int:
    body = load_body(ns, "metric-source update")
    resp = ctx.client.request(
        "POST", f"/metrics/metric_source/{quote_path_part(ns.name)}", json_body=body
    )
    print(render_one((resp or {}).get("data", resp), resolve_fmt(ns)))
    return 0


def cmd_delete(ctx: Context, ns) -> int:
    resp = ctx.client.request("DELETE", f"/metrics/metric_source/{quote_path_part(ns.name)}")
    print(render(resp or {"message": f"deleted metric source {ns.name}"}, resolve_fmt(ns)))
    return 0
