"""`datus ecs tasks ...` — list/describe/run/stop tasks and read their logs."""

from __future__ import annotations

import argparse

from datus_aws_common import (
    PluginError,
    UsageError,
    add_output_option,
    call,
    eprint,
    paginate,
    render_one,
    render_rows,
    wait_until,
)


def register(sub: argparse._SubParsersAction) -> None:
    tasks = sub.add_parser("tasks", help="ECS tasks: list, describe, run, stop, logs")
    group = tasks.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list task ARNs in a cluster")
    p.add_argument("cluster")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one task")
    p.add_argument("cluster")
    p.add_argument("task")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("run", help="run a one-off task (starts billed compute)")
    p.add_argument("cluster", nargs="?", help="cluster (defaults to the profile's cluster)")
    p.add_argument("--task-def", required=True, help="task definition family[:revision]")
    p.add_argument("--launch-type", choices=["FARGATE", "EC2"], default="FARGATE")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--subnet", action="append", dest="subnets", help="subnet id (repeatable; awsvpc)")
    p.add_argument("--security-group", action="append", dest="security_groups", help="security group id (repeatable)")
    p.add_argument("--assign-public-ip", action="store_true")
    p.add_argument("--wait", action="store_true", help="poll until the task stops")
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=900.0)
    p.set_defaults(func=cmd_run)

    p = group.add_parser("stop", help="stop a running task")
    p.add_argument("cluster")
    p.add_argument("task")
    p.add_argument("--reason", default="stopped via datus ecs")
    p.set_defaults(func=cmd_stop)

    p = group.add_parser("logs", help="read a task container's CloudWatch logs (needs log_group)")
    p.add_argument("cluster")
    p.add_argument("task")
    p.add_argument("--container", required=True, help="container name")
    p.add_argument("--stream-prefix", default="ecs", help="awslogs stream prefix (default 'ecs')")
    p.set_defaults(func=cmd_logs)


def cmd_list(ctx, ns) -> int:
    arns = paginate(ctx.client("ecs"), "list_tasks", "taskArns", limit=ns.limit, cluster=ns.cluster)
    rows = [{"taskArn": a} for a in arns]
    print(render_rows(rows, ["taskArn"], ns.output))
    return 0


def _one_task(ctx, cluster, task):
    resp = call(ctx.client("ecs").describe_tasks, cluster=cluster, tasks=[task])
    tasks = resp.get("tasks", [])
    if not tasks:
        raise PluginError(f"task not found: {task}")
    return tasks[0]


def cmd_describe(ctx, ns) -> int:
    print(render_one(_one_task(ctx, ns.cluster, ns.task), ns.output))
    return 0


def cmd_run(ctx, ns) -> int:
    cluster = ns.cluster or ctx.settings.cluster
    if not cluster:
        raise UsageError("no cluster (arg or config cluster)")
    kwargs = {"cluster": cluster, "taskDefinition": ns.task_def, "count": ns.count, "launchType": ns.launch_type}
    if ns.subnets:
        awsvpc = {"subnets": ns.subnets, "assignPublicIp": "ENABLED" if ns.assign_public_ip else "DISABLED"}
        if ns.security_groups:
            awsvpc["securityGroups"] = ns.security_groups
        kwargs["networkConfiguration"] = {"awsvpcConfiguration": awsvpc}

    resp = call(ctx.client("ecs").run_task, **kwargs)
    started = resp.get("tasks", [])
    if not started:
        raise PluginError(f"run_task started no tasks: {resp.get('failures')}")
    task_arn = started[0]["taskArn"]
    print(f"started task {task_arn}")
    if not ns.wait:
        print(task_arn)
        return 0

    final = wait_until(
        lambda: _one_task(ctx, cluster, task_arn),
        lambda t: t.get("lastStatus") == "STOPPED",
        timeout=ns.timeout, interval=ns.interval,
        on_change=lambda t: eprint(f"task: {t.get('lastStatus')}"),
    )
    codes = [c.get("exitCode") for c in final.get("containers", [])]
    print(f"task stopped, exit codes: {codes}")
    return 0 if all((c == 0) for c in codes) else 1


def cmd_stop(ctx, ns) -> int:
    call(ctx.client("ecs").stop_task, cluster=ns.cluster, task=ns.task, reason=ns.reason)
    print(f"stopping task {ns.task}")
    return 0


def cmd_logs(ctx, ns) -> int:
    if not ctx.settings.log_group:
        raise UsageError("no log_group configured — set it in the profile to read task logs")
    task_id = ns.task.split("/")[-1]
    stream = f"{ns.stream_prefix}/{ns.container}/{task_id}"
    resp = call(ctx.client("logs").get_log_events, logGroupName=ctx.settings.log_group, logStreamName=stream, startFromHead=True)
    for event in resp.get("events", []):
        print(f"{event.get('timestamp')}  {(event.get('message') or '').rstrip(chr(10))}")
    return 0
