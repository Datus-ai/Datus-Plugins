"""`datus emr steps ...` — submit/monitor steps on a cluster and read step logs."""

from __future__ import annotations

import argparse
import gzip
import shlex

from datus_aws_common import (
    UsageError,
    add_output_option,
    call,
    eprint,
    paginate,
    render_one,
    render_rows,
    wait_until,
)

TERMINAL_STEP_STATES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}


def register(sub: argparse._SubParsersAction) -> None:
    steps = sub.add_parser("steps", help="EMR steps: list, describe, add, cancel, logs")
    group = steps.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list steps on a cluster")
    p.add_argument("cluster_id")
    p.add_argument("--state", action="append", dest="states", help="filter by step state (repeatable)")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one step")
    p.add_argument("cluster_id")
    p.add_argument("step_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("add", help="submit a step to a cluster (runs billed work)")
    p.add_argument("cluster_id", nargs="?", help="cluster id (defaults to the profile's cluster_id)")
    p.add_argument("--name", required=True)
    p.add_argument("--jar", help="path/URI of the JAR to run")
    p.add_argument("--arg", action="append", dest="args", help="argument to the JAR (repeatable)")
    p.add_argument("--main-class", help="main class in the JAR")
    p.add_argument("--command", help="a command run via command-runner.jar, e.g. 'spark-submit s3://b/job.py'")
    p.add_argument("--action-on-failure", choices=["CONTINUE", "CANCEL_AND_WAIT", "TERMINATE_CLUSTER"], default="CONTINUE")
    p.add_argument("--wait", action="store_true", help="poll until the step reaches a terminal state")
    p.add_argument("--interval", type=float, default=15.0)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.set_defaults(func=cmd_add)

    p = group.add_parser("cancel", help="cancel a pending/running step")
    p.add_argument("cluster_id")
    p.add_argument("step_id")
    p.set_defaults(func=cmd_cancel)

    p = group.add_parser("logs", help="read a step's stdout/stderr from S3 (needs log_uri)")
    p.add_argument("cluster_id")
    p.add_argument("step_id")
    p.add_argument("--stderr", action="store_true", help="read stderr instead of stdout")
    p.set_defaults(func=cmd_logs)


def cmd_list(ctx, ns) -> int:
    kwargs = {"ClusterId": ns.cluster_id}
    if ns.states:
        kwargs["StepStates"] = ns.states
    rows = paginate(ctx.client("emr"), "list_steps", "Steps", limit=ns.limit, **kwargs)
    if ns.output in ("json", "yaml"):
        print(render_rows(rows, None, ns.output))
        return 0
    view = [{"Id": s.get("Id"), "Name": s.get("Name"), "State": s.get("Status", {}).get("State")} for s in rows]
    print(render_rows(view, ["Id", "Name", "State"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    step = call(ctx.client("emr").describe_step, ClusterId=ns.cluster_id, StepId=ns.step_id)["Step"]
    print(render_one(step, ns.output))
    return 0


def cmd_add(ctx, ns) -> int:
    cluster = ns.cluster_id or ctx.settings.cluster_id
    if not cluster:
        raise UsageError("no cluster id (arg or config cluster_id)")

    if ns.command:
        hadoop = {"Jar": "command-runner.jar", "Args": shlex.split(ns.command)}
    elif ns.jar:
        hadoop = {"Jar": ns.jar, "Args": ns.args or []}
        if ns.main_class:
            hadoop["MainClass"] = ns.main_class
    else:
        raise UsageError("provide --command or --jar")

    step = {"Name": ns.name, "ActionOnFailure": ns.action_on_failure, "HadoopJarStep": hadoop}
    step_id = call(ctx.client("emr").add_job_flow_steps, JobFlowId=cluster, Steps=[step])["StepIds"][0]
    print(f"added step {step_id}")
    if not ns.wait:
        print(step_id)
        return 0

    final = wait_until(
        lambda: call(ctx.client("emr").describe_step, ClusterId=cluster, StepId=step_id)["Step"],
        lambda s: s.get("Status", {}).get("State") in TERMINAL_STEP_STATES,
        timeout=ns.timeout, interval=ns.interval,
        on_change=lambda s: eprint(f"step {step_id}: {s.get('Status', {}).get('State')}"),
    )
    state = final.get("Status", {}).get("State")
    print(f"step {step_id}: {state}")
    return 0 if state == "COMPLETED" else 1


def cmd_cancel(ctx, ns) -> int:
    call(ctx.client("emr").cancel_steps, ClusterId=ns.cluster_id, StepIds=[ns.step_id])
    print(f"requested cancel of step {ns.step_id}")
    return 0


def _parse_s3_uri(uri: str):
    rest = uri[len("s3://"):] if uri.startswith("s3://") else uri
    bucket, _, prefix = rest.partition("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def cmd_logs(ctx, ns) -> int:
    if not ctx.settings.log_uri:
        raise UsageError("no log_uri configured — set it in the profile to read step logs")
    bucket, prefix = _parse_s3_uri(ctx.settings.log_uri)
    which = "stderr.gz" if ns.stderr else "stdout.gz"
    key = f"{prefix}{ns.cluster_id}/steps/{ns.step_id}/{which}"
    resp = call(ctx.client("s3").get_object, Bucket=bucket, Key=key)
    data = gzip.decompress(resp["Body"].read())
    print(data.decode("utf-8", "replace"))
    return 0
