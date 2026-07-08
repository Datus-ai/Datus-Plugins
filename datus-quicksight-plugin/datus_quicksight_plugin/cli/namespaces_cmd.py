"""`datus quicksight namespaces ...` — QuickSight namespaces."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, render_one, render_rows

from ._helpers import acct, qs_identity


def register(sub: argparse._SubParsersAction) -> None:
    namespaces = sub.add_parser("namespaces", help="QuickSight namespaces: list/describe/create/delete")
    group = namespaces.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list namespaces")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one namespace")
    p.add_argument("namespace")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("create", help="create a namespace (QUICKSIGHT identity store)")
    p.add_argument("namespace")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("delete", help="delete a namespace")
    p.add_argument("namespace")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs_identity(ctx), "list_namespaces", "Namespaces", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["Name", "CreationStatus", "IdentityStore"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    resp = call(qs_identity(ctx).describe_namespace, AwsAccountId=acct(ctx), Namespace=ns.namespace)
    print(render_one(resp.get("Namespace", {}), ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    call(qs_identity(ctx).create_namespace, AwsAccountId=acct(ctx), Namespace=ns.namespace, IdentityStore="QUICKSIGHT")
    print(f"creating namespace {ns.namespace}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete namespace {ns.namespace}?", ns.yes):
        print("aborted")
        return 1
    call(qs_identity(ctx).delete_namespace, AwsAccountId=acct(ctx), Namespace=ns.namespace)
    print(f"deleted namespace {ns.namespace}")
    return 0
