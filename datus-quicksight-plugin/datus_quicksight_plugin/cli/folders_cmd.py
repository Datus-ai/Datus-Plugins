"""`datus quicksight folders ...` — folders and their membership."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, qs

MEMBER_TYPES = ["DASHBOARD", "ANALYSIS", "DATASET", "DATASOURCE"]


def register(sub: argparse._SubParsersAction) -> None:
    folders = sub.add_parser("folders", help="QuickSight folders: list/describe/create/delete + membership")
    group = folders.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list folders")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one folder")
    p.add_argument("folder_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("members", help="list a folder's members")
    p.add_argument("folder_id")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_members)

    p = group.add_parser("create", help="create a folder from a JSON request body")
    p.add_argument("--cli-input", required=True, help="CreateFolder request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("delete", help="delete a folder")
    p.add_argument("folder_id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("member-add", help="add an asset to a folder")
    p.add_argument("folder_id")
    p.add_argument("member_id")
    p.add_argument("--member-type", choices=MEMBER_TYPES, required=True)
    p.set_defaults(func=cmd_member_add)

    p = group.add_parser("member-remove", help="remove an asset from a folder")
    p.add_argument("folder_id")
    p.add_argument("member_id")
    p.add_argument("--member-type", choices=MEMBER_TYPES, required=True)
    p.set_defaults(func=cmd_member_remove)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_folders", "FolderSummaryList", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["FolderId", "Name", "FolderType", "LastUpdatedTime"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    folder = call(qs(ctx).describe_folder, AwsAccountId=acct(ctx), FolderId=ns.folder_id)["Folder"]
    print(render_one(folder, ns.output))
    return 0


def cmd_members(ctx, ns) -> int:
    rows = paginate(
        qs(ctx), "list_folder_members", "FolderMemberList",
        limit=ns.limit, AwsAccountId=acct(ctx), FolderId=ns.folder_id,
    )
    print(render_rows(rows, ["MemberId", "MemberArn"], ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_folder, AwsAccountId=acct(ctx), **body)
    print(f"created folder {resp.get('FolderId')}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete folder {ns.folder_id}?", ns.yes):
        print("aborted")
        return 1
    call(qs(ctx).delete_folder, AwsAccountId=acct(ctx), FolderId=ns.folder_id)
    print(f"deleted folder {ns.folder_id}")
    return 0


def cmd_member_add(ctx, ns) -> int:
    call(qs(ctx).create_folder_membership, AwsAccountId=acct(ctx), FolderId=ns.folder_id,
         MemberId=ns.member_id, MemberType=ns.member_type)
    print(f"added {ns.member_type} {ns.member_id} to folder {ns.folder_id}")
    return 0


def cmd_member_remove(ctx, ns) -> int:
    call(qs(ctx).delete_folder_membership, AwsAccountId=acct(ctx), FolderId=ns.folder_id,
         MemberId=ns.member_id, MemberType=ns.member_type)
    print(f"removed {ns.member_type} {ns.member_id} from folder {ns.folder_id}")
    return 0
