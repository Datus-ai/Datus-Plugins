"""`datus quicksight templates ...` — full template lifecycle."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    templates = sub.add_parser("templates", help="QuickSight templates: list/describe/versions/create/update/delete")
    group = templates.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list templates")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one template")
    p.add_argument("template_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("versions", help="list a template's versions")
    p.add_argument("template_id")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_versions)

    p = group.add_parser("create", help="create a template from a JSON request body")
    p.add_argument("--cli-input", required=True, help="CreateTemplate request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("update", help="update a template from a JSON request body")
    p.add_argument("--cli-input", required=True, help="UpdateTemplate request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_update)

    p = group.add_parser("delete", help="delete a template")
    p.add_argument("template_id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_templates", "TemplateSummaryList", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["TemplateId", "Name", "LatestVersionNumber", "LastUpdatedTime"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    template = call(qs(ctx).describe_template, AwsAccountId=acct(ctx), TemplateId=ns.template_id)["Template"]
    print(render_one(template, ns.output))
    return 0


def cmd_versions(ctx, ns) -> int:
    rows = paginate(
        qs(ctx), "list_template_versions", "TemplateVersionSummaryList",
        limit=ns.limit, AwsAccountId=acct(ctx), TemplateId=ns.template_id,
    )
    print(render_rows(rows, ["VersionNumber", "Status", "CreatedTime"], ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_template, AwsAccountId=acct(ctx), **body)
    print(f"created template {resp.get('TemplateId')}")
    return 0


def cmd_update(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).update_template, AwsAccountId=acct(ctx), **body)
    print(f"updated template {resp.get('TemplateId')}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete template {ns.template_id}?", ns.yes):
        print("aborted")
        return 1
    call(qs(ctx).delete_template, AwsAccountId=acct(ctx), TemplateId=ns.template_id)
    print(f"deleted template {ns.template_id}")
    return 0
