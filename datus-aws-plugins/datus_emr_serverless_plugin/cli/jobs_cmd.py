"""`datus emr-serverless jobs ...` — run/monitor Spark job runs and the live UI."""

from __future__ import annotations

import argparse

from datus_aws_common import (
    UsageError,
    add_output_option,
    call,
    eprint,
    paginate,
    parse_json_arg,
    render_one,
    render_rows,
    wait_until,
)

TERMINAL_JOB_STATES = {"SUCCESS", "FAILED", "CANCELLED"}


def register(sub: argparse._SubParsersAction) -> None:
    jobs = sub.add_parser("jobs", help="EMR Serverless job runs: run, list, monitor, dashboard")
    group = jobs.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("run", help="submit a Spark job run (writes data, billed)")
    p.add_argument("app_id", nargs="?", help="application id (defaults to the profile's application_id)")
    p.add_argument("--entry-point", required=True, help="entry point (e.g. s3://bkt/job.py or a JAR)")
    p.add_argument("--entry-point-args", help="entry point arguments as a JSON array")
    p.add_argument("--spark-submit-params", help="extra spark-submit parameters string")
    p.add_argument("--execution-role", help="IAM role ARN (defaults to the profile's execution_role_arn)")
    p.add_argument("--name", help="a name for the job run")
    p.add_argument("--wait", action="store_true", help="poll until the run reaches a terminal state")
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.set_defaults(func=cmd_run)

    p = group.add_parser("list", help="list job runs of an application")
    p.add_argument("app_id")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("run-status", help="status of one job run")
    p.add_argument("app_id")
    p.add_argument("run_id")
    add_output_option(p)
    p.set_defaults(func=cmd_run_status)

    p = group.add_parser("cancel", help="cancel a running job run")
    p.add_argument("app_id")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_cancel)

    p = group.add_parser("dashboard", help="get the live Spark UI URL for a job run")
    p.add_argument("app_id")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_dashboard)


def cmd_run(ctx, ns) -> int:
    client = ctx.client("emr-serverless")
    application_id = ns.app_id or ctx.settings.application_id
    if not application_id:
        raise UsageError("no application id (arg or config)")
    role = ns.execution_role or ctx.settings.execution_role_arn
    if not role:
        raise UsageError("no execution role (--execution-role or config)")

    spark = {"entryPoint": ns.entry_point}
    if ns.entry_point_args:
        args = parse_json_arg(ns.entry_point_args, "--entry-point-args")
        if not isinstance(args, list):
            raise UsageError("--entry-point-args must be a JSON array")
        spark["entryPointArguments"] = args
    if ns.spark_submit_params:
        spark["sparkSubmitParameters"] = ns.spark_submit_params
    job_driver = {"sparkSubmit": spark}

    kwargs = {"applicationId": application_id, "executionRoleArn": role, "jobDriver": job_driver}
    if ns.name:
        kwargs["name"] = ns.name

    run_id = call(client.start_job_run, **kwargs)["jobRunId"]
    print(f"started job run {run_id}")
    if not ns.wait:
        print(run_id)
        return 0

    final = wait_until(
        lambda: call(client.get_job_run, applicationId=application_id, jobRunId=run_id)["jobRun"],
        lambda jr: jr.get("state") in TERMINAL_JOB_STATES,
        timeout=ns.timeout, interval=ns.interval,
        on_change=lambda jr: eprint(f"run {run_id}: {jr.get('state')}"),
    )
    state = final.get("state")
    print(f"run {run_id}: {state}")
    if state != "SUCCESS" and final.get("stateDetails"):
        eprint(final["stateDetails"])
    return 0 if state == "SUCCESS" else 1


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("emr-serverless"), "list_job_runs", "jobRuns", limit=ns.limit, applicationId=ns.app_id)
    print(render_rows(rows, ["id", "name", "state", "createdAt"], ns.output))
    return 0


def cmd_run_status(ctx, ns) -> int:
    job_run = call(ctx.client("emr-serverless").get_job_run, applicationId=ns.app_id, jobRunId=ns.run_id)["jobRun"]
    print(render_one(job_run, ns.output))
    return 0


def cmd_cancel(ctx, ns) -> int:
    call(ctx.client("emr-serverless").cancel_job_run, applicationId=ns.app_id, jobRunId=ns.run_id)
    print(f"cancelled job run {ns.run_id}")
    return 0


def cmd_dashboard(ctx, ns) -> int:
    url = call(
        ctx.client("emr-serverless").get_dashboard_for_job_run,
        applicationId=ns.app_id, jobRunId=ns.run_id,
    ).get("url")
    print(url)
    return 0
