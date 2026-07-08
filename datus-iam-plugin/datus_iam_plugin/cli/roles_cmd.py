"""`datus iam roles ...` — list/inspect roles, attached policies, trust policy."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    roles = sub.add_parser("roles", help="IAM roles: list, get, attached, trust")
    group = roles.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list roles")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one role")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("attached", help="list a role's attached managed policies")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_attached)

    p = group.add_parser("trust", help="show a role's trust (assume-role) policy")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_trust)


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("iam"), "list_roles", "Roles", limit=ns.limit)
    print(render_rows(rows, ["RoleName", "Arn", "CreateDate"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    role = call(ctx.client("iam").get_role, RoleName=ns.name)["Role"]
    print(render_one(role, ns.output))
    return 0


def cmd_attached(ctx, ns) -> int:
    rows = paginate(ctx.client("iam"), "list_attached_role_policies", "AttachedPolicies", RoleName=ns.name)
    print(render_rows(rows, ["PolicyName", "PolicyArn"], ns.output))
    return 0


def cmd_trust(ctx, ns) -> int:
    role = call(ctx.client("iam").get_role, RoleName=ns.name)["Role"]
    print(render_one(role.get("AssumeRolePolicyDocument", {}), ns.output))
    return 0
