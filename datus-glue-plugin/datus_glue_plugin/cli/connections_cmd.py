"""`datus glue connections ...` — list and get Data Catalog connections."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def _cat(ctx) -> dict:
    return {"CatalogId": ctx.settings.catalog_id} if ctx.settings.catalog_id else {}


def _mask(connection: dict) -> dict:
    props = connection.get("ConnectionProperties")
    if isinstance(props, dict):
        for key in list(props):
            if "PASSWORD" in key.upper() or "SECRET" in key.upper():
                props[key] = "***"
    return connection


def register(sub: argparse._SubParsersAction) -> None:
    connections = sub.add_parser("connections", help="Glue Data Catalog connections: list, get")
    group = connections.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list connections")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="get a connection (secrets masked)")
    p.add_argument("name")
    p.add_argument("--show-secrets", action="store_true", help="reveal password/secret properties")
    add_output_option(p)
    p.set_defaults(func=cmd_get)


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("glue"), "get_connections", "ConnectionList", limit=ns.limit, **_cat(ctx))
    for conn in rows:
        _mask(conn)
    print(render_rows(rows, ["Name", "ConnectionType", "LastUpdatedTime"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    conn = call(ctx.client("glue").get_connection, Name=ns.name, **_cat(ctx))["Connection"]
    if not ns.show_secrets:
        _mask(conn)
    print(render_one(conn, ns.output))
    return 0
