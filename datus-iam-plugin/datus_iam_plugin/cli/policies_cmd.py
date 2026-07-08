"""`datus iam policies ...` — list/inspect managed policies and their documents."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    policies = sub.add_parser("policies", help="IAM managed policies: list, get, document")
    group = policies.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list managed policies")
    p.add_argument("--scope", choices=["Local", "AWS", "All"], default="Local",
                   help="Local (customer-managed, default), AWS, or All")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one policy by ARN")
    p.add_argument("arn")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("document", help="print a policy's default-version JSON document")
    p.add_argument("arn")
    add_output_option(p)
    p.set_defaults(func=cmd_document)


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("iam"), "list_policies", "Policies", limit=ns.limit, Scope=ns.scope)
    print(render_rows(rows, ["PolicyName", "Arn", "AttachmentCount"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    policy = call(ctx.client("iam").get_policy, PolicyArn=ns.arn)["Policy"]
    print(render_one(policy, ns.output))
    return 0


def cmd_document(ctx, ns) -> int:
    client = ctx.client("iam")
    version = call(client.get_policy, PolicyArn=ns.arn)["Policy"]["DefaultVersionId"]
    document = call(client.get_policy_version, PolicyArn=ns.arn, VersionId=version)["PolicyVersion"]["Document"]
    print(render_one(document, ns.output))
    return 0
