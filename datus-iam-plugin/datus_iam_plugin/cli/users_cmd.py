"""`datus iam users ...` — list/inspect users and their attached policies."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    users = sub.add_parser("users", help="IAM users: list, get, attached")
    group = users.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list users")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one user")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("attached", help="list a user's attached managed policies")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_attached)


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("iam"), "list_users", "Users", limit=ns.limit)
    print(render_rows(rows, ["UserName", "Arn", "CreateDate"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    user = call(ctx.client("iam").get_user, UserName=ns.name)["User"]
    print(render_one(user, ns.output))
    return 0


def cmd_attached(ctx, ns) -> int:
    rows = paginate(ctx.client("iam"), "list_attached_user_policies", "AttachedPolicies", UserName=ns.name)
    print(render_rows(rows, ["PolicyName", "PolicyArn"], ns.output))
    return 0
