"""`datus ecs task-defs ...` — list and describe task definitions."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    taskdefs = sub.add_parser("task-defs", help="ECS task definitions: list, describe")
    group = taskdefs.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list task definition ARNs")
    p.add_argument("--family", help="family prefix filter")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe a task definition")
    p.add_argument("task_def", help="family[:revision] or ARN")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)


def cmd_list(ctx, ns) -> int:
    kwargs = {}
    if ns.family:
        kwargs["familyPrefix"] = ns.family
    arns = paginate(ctx.client("ecs"), "list_task_definitions", "taskDefinitionArns", limit=ns.limit, **kwargs)
    print(render_rows([{"taskDefinitionArn": a} for a in arns], ["taskDefinitionArn"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    resp = call(ctx.client("ecs").describe_task_definition, taskDefinition=ns.task_def)
    print(render_one(resp.get("taskDefinition", {}), ns.output))
    return 0
