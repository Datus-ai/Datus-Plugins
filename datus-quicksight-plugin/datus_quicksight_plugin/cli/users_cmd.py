"""`datus quicksight users ...` — QuickSight users (identity region + namespace)."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, namespace, qs_identity


def register(sub: argparse._SubParsersAction) -> None:
    users = sub.add_parser("users", help="QuickSight users: list/describe/register/update/delete")
    group = users.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list users in the namespace")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one user")
    p.add_argument("user_name")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("register", help="register a user from a JSON request body")
    p.add_argument("--cli-input", required=True, help="RegisterUser request as JSON (without AwsAccountId/Namespace)")
    p.set_defaults(func=cmd_register)

    p = group.add_parser("update", help="update a user from a JSON request body")
    p.add_argument("--cli-input", required=True, help="UpdateUser request as JSON (without AwsAccountId/Namespace)")
    p.set_defaults(func=cmd_update)

    p = group.add_parser("delete", help="delete a user")
    p.add_argument("user_name")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)


def cmd_list(ctx, ns) -> int:
    rows = paginate(
        qs_identity(ctx), "list_users", "UserList",
        limit=ns.limit, AwsAccountId=acct(ctx), Namespace=namespace(ctx),
    )
    print(render_rows(rows, ["UserName", "Role", "Email", "Active"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    user = call(qs_identity(ctx).describe_user, AwsAccountId=acct(ctx), Namespace=namespace(ctx), UserName=ns.user_name)["User"]
    print(render_one(user, ns.output))
    return 0


def cmd_register(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs_identity(ctx).register_user, AwsAccountId=acct(ctx), Namespace=namespace(ctx), **body)
    print(f"registered user {resp.get('User', {}).get('UserName')}")
    return 0


def cmd_update(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    call(qs_identity(ctx).update_user, AwsAccountId=acct(ctx), Namespace=namespace(ctx), **body)
    print(f"updated user {body.get('UserName')}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete user {ns.user_name}?", ns.yes):
        print("aborted")
        return 1
    call(qs_identity(ctx).delete_user, AwsAccountId=acct(ctx), Namespace=namespace(ctx), UserName=ns.user_name)
    print(f"deleted user {ns.user_name}")
    return 0
