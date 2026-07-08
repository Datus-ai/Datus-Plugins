"""`datus statsig warehouse-connections ...` — update warehouse connection credentials."""

from __future__ import annotations

import argparse

from ..output import render
from ..schemas import epilog_for
from . import Context, add_output_option, load_body, resolve_fmt


def register(sub: argparse._SubParsersAction) -> None:
    wc = sub.add_parser("warehouse-connections", help="update warehouse connection credentials")
    group = wc.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser(
        "update",
        help="update warehouse connection credentials (confirms; --json-file only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_for("warehouse-connections update"),
    )
    # Credentials only from a file — never inline on the command line.
    p.add_argument(
        "--json-file",
        dest="json_file",
        required=True,
        help="request body from a JSON file (contains credentials — delete after use)",
    )
    add_output_option(p)
    p.set_defaults(func=cmd_update)


def cmd_update(ctx: Context, ns) -> int:
    body = load_body(ns, "warehouse-connections update")
    resp = ctx.client.request("PATCH", "/wh_connections", json_body=body)
    print(render(resp or {"message": "warehouse connection updated"}, resolve_fmt(ns)))
    return 0
