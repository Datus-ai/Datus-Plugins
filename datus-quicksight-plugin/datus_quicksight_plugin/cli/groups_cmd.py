"""`datus quicksight groups ...` — QuickSight groups and memberships."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, render_one, render_rows

from ._helpers import acct, namespace, qs_identity


def register(sub: argparse._SubParsersAction) -> None:
    groups = sub.add_parser("groups", help="QuickSight groups: list/describe/create/delete + membership")
    group = groups.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list groups in the namespace")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one group")
    p.add_argument("group_name")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("members", help="list a group's members")
    p.add_argument("group_name")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_members)

    p = group.add_parser("create", help="create a group")
    p.add_argument("group_name")
    p.add_argument("--description")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("delete", help="delete a group")
    p.add_argument("group_name")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("member-add", help="add a user to a group")
    p.add_argument("group_name")
    p.add_argument("member_name")
    p.set_defaults(func=cmd_member_add)

    p = group.add_parser("member-remove", help="remove a user from a group")
    p.add_argument("group_name")
    p.add_argument("member_name")
    p.set_defaults(func=cmd_member_remove)


def cmd_list(ctx, ns) -> int:
    rows = paginate(
        qs_identity(ctx), "list_groups", "GroupList",
        limit=ns.limit, AwsAccountId=acct(ctx), Namespace=namespace(ctx),
    )
    print(render_rows(rows, ["GroupName", "Description", "PrincipalId"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    grp = call(qs_identity(ctx).describe_group, AwsAccountId=acct(ctx), Namespace=namespace(ctx), GroupName=ns.group_name)["Group"]
    print(render_one(grp, ns.output))
    return 0


def cmd_members(ctx, ns) -> int:
    rows = paginate(
        qs_identity(ctx), "list_group_memberships", "GroupMemberList",
        limit=ns.limit, AwsAccountId=acct(ctx), Namespace=namespace(ctx), GroupName=ns.group_name,
    )
    print(render_rows(rows, ["MemberName", "Arn"], ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    kwargs = {"AwsAccountId": acct(ctx), "Namespace": namespace(ctx), "GroupName": ns.group_name}
    if ns.description:
        kwargs["Description"] = ns.description
    call(qs_identity(ctx).create_group, **kwargs)
    print(f"created group {ns.group_name}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete group {ns.group_name}?", ns.yes):
        print("aborted")
        return 1
    call(qs_identity(ctx).delete_group, AwsAccountId=acct(ctx), Namespace=namespace(ctx), GroupName=ns.group_name)
    print(f"deleted group {ns.group_name}")
    return 0


def cmd_member_add(ctx, ns) -> int:
    call(qs_identity(ctx).create_group_membership, AwsAccountId=acct(ctx), Namespace=namespace(ctx),
         GroupName=ns.group_name, MemberName=ns.member_name)
    print(f"added {ns.member_name} to group {ns.group_name}")
    return 0


def cmd_member_remove(ctx, ns) -> int:
    call(qs_identity(ctx).delete_group_membership, AwsAccountId=acct(ctx), Namespace=namespace(ctx),
         GroupName=ns.group_name, MemberName=ns.member_name)
    print(f"removed {ns.member_name} from group {ns.group_name}")
    return 0
