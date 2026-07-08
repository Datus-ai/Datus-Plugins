"""`datus quicksight datasources ...` — full data source lifecycle."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    datasources = sub.add_parser("datasources", help="QuickSight data sources: list/describe/create/update/delete")
    group = datasources.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list data sources")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one data source")
    p.add_argument("datasource_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("create", help="create a data source from a JSON request body")
    p.add_argument("--cli-input", required=True, help="CreateDataSource request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("update", help="update a data source from a JSON request body")
    p.add_argument("--cli-input", required=True, help="UpdateDataSource request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_update)

    p = group.add_parser("delete", help="delete a data source")
    p.add_argument("datasource_id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_data_sources", "DataSources", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["DataSourceId", "Name", "Type", "Status"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    ds = call(qs(ctx).describe_data_source, AwsAccountId=acct(ctx), DataSourceId=ns.datasource_id)["DataSource"]
    print(render_one(ds, ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_data_source, AwsAccountId=acct(ctx), **body)
    print(f"created data source {resp.get('DataSourceId')}")
    return 0


def cmd_update(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).update_data_source, AwsAccountId=acct(ctx), **body)
    print(f"updated data source {resp.get('DataSourceId')}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete data source {ns.datasource_id}?", ns.yes):
        print("aborted")
        return 1
    call(qs(ctx).delete_data_source, AwsAccountId=acct(ctx), DataSourceId=ns.datasource_id)
    print(f"deleted data source {ns.datasource_id}")
    return 0
