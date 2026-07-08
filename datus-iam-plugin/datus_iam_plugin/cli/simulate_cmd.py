"""`datus iam simulate ...` — the AccessDenied diagnostic: can a principal do X?"""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, parse_json_arg, render_rows

_COLUMNS = ["EvalActionName", "EvalDecision", "EvalResourceName"]


def register(sub: argparse._SubParsersAction) -> None:
    simulate = sub.add_parser("simulate", help="simulate whether actions are allowed")
    group = simulate.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("principal", help="simulate an existing principal's effective permissions")
    p.add_argument("arn", help="principal ARN (role/user)")
    p.add_argument("--action", action="append", dest="actions", required=True,
                   help="action to test, e.g. s3:GetObject (repeatable)")
    p.add_argument("--resource", action="append", dest="resources",
                   help="resource ARN (repeatable; default '*')")
    add_output_option(p)
    p.set_defaults(func=cmd_principal)

    p = group.add_parser("custom", help="simulate a standalone policy document")
    p.add_argument("--policy", required=True, help="a policy JSON document")
    p.add_argument("--action", action="append", dest="actions", required=True, help="action (repeatable)")
    p.add_argument("--resource", action="append", dest="resources", help="resource ARN (repeatable; default '*')")
    add_output_option(p)
    p.set_defaults(func=cmd_custom)


def cmd_principal(ctx, ns) -> int:
    resp = call(
        ctx.client("iam").simulate_principal_policy,
        PolicySourceArn=ns.arn,
        ActionNames=ns.actions,
        ResourceArns=ns.resources or ["*"],
    )
    print(render_rows(resp.get("EvaluationResults", []), _COLUMNS, ns.output))
    return 0


def cmd_custom(ctx, ns) -> int:
    parse_json_arg(ns.policy, "--policy")  # validate it is JSON; the API takes the raw string
    resp = call(
        ctx.client("iam").simulate_custom_policy,
        PolicyInputList=[ns.policy],
        ActionNames=ns.actions,
        ResourceArns=ns.resources or ["*"],
    )
    print(render_rows(resp.get("EvaluationResults", []), _COLUMNS, ns.output))
    return 0
