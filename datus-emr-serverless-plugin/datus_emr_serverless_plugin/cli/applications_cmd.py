"""`datus emr-serverless applications ...` — list/inspect/start/stop applications."""

from __future__ import annotations

import argparse

from datus_aws_common import (
    add_output_option,
    call,
    paginate,
    render_one,
    render_rows,
)


def register(sub: argparse._SubParsersAction) -> None:
    apps = sub.add_parser("applications", help="EMR Serverless applications: list, get, start, stop")
    group = apps.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list applications")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one application")
    p.add_argument("app_id")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("start", help="start an application (so it can run jobs)")
    p.add_argument("app_id")
    p.set_defaults(func=cmd_start)

    p = group.add_parser("stop", help="stop an application")
    p.add_argument("app_id")
    p.set_defaults(func=cmd_stop)


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("emr-serverless"), "list_applications", "applications", limit=ns.limit)
    print(render_rows(rows, ["id", "name", "state", "type", "architecture"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    app = call(ctx.client("emr-serverless").get_application, applicationId=ns.app_id)["application"]
    print(render_one(app, ns.output))
    return 0


def cmd_start(ctx, ns) -> int:
    call(ctx.client("emr-serverless").start_application, applicationId=ns.app_id)
    print(f"started application {ns.app_id}")
    return 0


def cmd_stop(ctx, ns) -> int:
    call(ctx.client("emr-serverless").stop_application, applicationId=ns.app_id)
    print(f"stopped application {ns.app_id}")
    return 0
