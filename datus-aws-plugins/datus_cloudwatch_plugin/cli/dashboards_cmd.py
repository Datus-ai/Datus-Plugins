"""`datus cloudwatch dashboards ...` — list and get dashboards."""

from __future__ import annotations

import argparse
import json

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    dashboards = sub.add_parser("dashboards", help="CloudWatch dashboards: list, get")
    group = dashboards.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list dashboards")
    p.add_argument("-p", "--prefix", help="dashboard name prefix")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="get a dashboard definition")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)


def cmd_list(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    kwargs = {}
    if ns.prefix:
        kwargs["DashboardNamePrefix"] = ns.prefix
    rows = paginate(client, "list_dashboards", "DashboardEntries", limit=ns.limit, **kwargs)
    print(render_rows(rows, ["DashboardName", "LastModified", "Size"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    resp = call(client.get_dashboard, DashboardName=ns.name)
    body = resp.get("DashboardBody")
    if body and ns.output in ("json", "yaml"):
        try:
            resp["DashboardBody"] = json.loads(body)
        except (ValueError, TypeError):
            pass
    print(render_one(resp, ns.output))
    return 0
