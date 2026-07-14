"""`datus quicksight datasets ...` — full dataset lifecycle + permissions."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    datasets = sub.add_parser("datasets", help="QuickSight datasets: list/describe/create/update/delete/permissions")
    group = datasets.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list datasets")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one dataset")
    p.add_argument("dataset_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("permissions", help="show a dataset's resource permissions")
    p.add_argument("dataset_id")
    add_output_option(p)
    p.set_defaults(func=cmd_permissions)

    p = group.add_parser("create", help="create a dataset from a JSON request body")
    p.add_argument("--cli-input", required=True, help="CreateDataSet request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("update", help="update a dataset from a JSON request body")
    p.add_argument("--cli-input", required=True, help="UpdateDataSet request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_update)

    p = group.add_parser("delete", help="delete a dataset")
    p.add_argument("dataset_id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("set-permissions", help="grant/revoke dataset permissions from a JSON body")
    p.add_argument("dataset_id")
    p.add_argument("--cli-input", required=True, help="UpdateDataSetPermissions body as JSON (GrantPermissions/RevokePermissions)")
    p.set_defaults(func=cmd_set_permissions)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_data_sets", "DataSetSummaries", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["DataSetId", "Name", "ImportMode", "LastUpdatedTime"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    ds = call(qs(ctx).describe_data_set, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id)["DataSet"]
    print(render_one(ds, ns.output))
    return 0


def cmd_permissions(ctx, ns) -> int:
    resp = call(qs(ctx).describe_data_set_permissions, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id)
    print(render_rows(resp.get("Permissions", []), ["Principal", "Actions"], ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_data_set, AwsAccountId=acct(ctx), **body)
    print(f"created dataset {resp.get('DataSetId')}")
    return 0


def cmd_update(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).update_data_set, AwsAccountId=acct(ctx), **body)
    print(f"updated dataset {resp.get('DataSetId')}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete dataset {ns.dataset_id}?", ns.yes):
        print("aborted")
        return 1
    call(qs(ctx).delete_data_set, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id)
    print(f"deleted dataset {ns.dataset_id}")
    return 0


def cmd_set_permissions(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    call(qs(ctx).update_data_set_permissions, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id, **body)
    print(f"updated permissions for dataset {ns.dataset_id}")
    return 0
