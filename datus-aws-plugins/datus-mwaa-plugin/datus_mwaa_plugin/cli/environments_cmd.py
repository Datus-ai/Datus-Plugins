"""`datus mwaa environments ...` — list and describe MWAA environments."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    environments = sub.add_parser("environments", help="MWAA environments: list, get")
    group = environments.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list environment names")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one environment")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)


def cmd_list(ctx, ns) -> int:
    names = paginate(ctx.client("mwaa"), "list_environments", "Environments", limit=ns.limit)
    print(render_rows([{"Name": n} for n in names], ["Name"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    env = call(ctx.client("mwaa").get_environment, Name=ns.name)["Environment"]
    print(render_one(env, ns.output))
    return 0
