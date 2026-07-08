"""`datus glue jobs ...` — list/run/monitor ETL jobs and read their logs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

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

TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}


def register(sub: argparse._SubParsersAction) -> None:
    jobs = sub.add_parser("jobs", help="Glue ETL jobs: list, run, monitor, logs")
    group = jobs.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list job definitions")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one job definition")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("run", help="start a job run (writes data, billed)")
    p.add_argument("name")
    p.add_argument("--args", help="job Arguments as a JSON object")
    p.add_argument("--wait", action="store_true", help="poll until the run reaches a terminal state")
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.set_defaults(func=cmd_run)

    p = group.add_parser("run-status", help="status of one job run")
    p.add_argument("name")
    p.add_argument("run_id")
    add_output_option(p)
    p.set_defaults(func=cmd_run_status)

    p = group.add_parser("runs", help="recent runs of a job")
    p.add_argument("name")
    p.add_argument("--state", help="filter by JobRunState")
    p.add_argument("--limit", type=int, default=20)
    add_output_option(p)
    p.set_defaults(func=cmd_runs)

    p = group.add_parser("stop", help="stop job run(s)")
    p.add_argument("name")
    p.add_argument("run_id", nargs="+")
    p.set_defaults(func=cmd_stop)

    p = group.add_parser("logs", help="read a run's CloudWatch logs")
    p.add_argument("name")
    p.add_argument("run_id")
    p.add_argument("--error", action="store_true", help="read the error log group instead of output")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_logs)

    p = group.add_parser("bookmark-reset", help="reset a job's bookmark (causes reprocessing)")
    p.add_argument("name")
    p.set_defaults(func=cmd_bookmark_reset)


def _fmt_ts(ms) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("glue"), "get_jobs", "Jobs", limit=ns.limit)
    print(render_rows(rows, ["Name", "Role", "GlueVersion", "WorkerType", "NumberOfWorkers"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    job = call(ctx.client("glue").get_job, JobName=ns.name)["Job"]
    print(render_one(job, ns.output))
    return 0


def cmd_run(ctx, ns) -> int:
    client = ctx.client("glue")
    kwargs = {"JobName": ns.name}
    if ns.args:
        args = parse_json_arg(ns.args, "--args")
        if not isinstance(args, dict):
            raise UsageError("--args must be a JSON object")
        kwargs["Arguments"] = args
    run_id = call(client.start_job_run, **kwargs)["JobRunId"]
    print(f"started job run {run_id}")
    if not ns.wait:
        print(run_id)
        return 0

    final = wait_until(
        lambda: call(client.get_job_run, JobName=ns.name, RunId=run_id)["JobRun"],
        lambda jr: jr.get("JobRunState") in TERMINAL_JOB_STATES,
        timeout=ns.timeout, interval=ns.interval,
        on_change=lambda jr: eprint(f"run {run_id}: {jr.get('JobRunState')}"),
    )
    state = final.get("JobRunState")
    print(f"run {run_id}: {state}")
    if state != "SUCCEEDED" and final.get("ErrorMessage"):
        eprint(final["ErrorMessage"])
    return 0 if state == "SUCCEEDED" else 1


def cmd_run_status(ctx, ns) -> int:
    job_run = call(ctx.client("glue").get_job_run, JobName=ns.name, RunId=ns.run_id)["JobRun"]
    print(render_one(job_run, ns.output))
    return 0


def cmd_runs(ctx, ns) -> int:
    rows = paginate(ctx.client("glue"), "get_job_runs", "JobRuns", limit=ns.limit, JobName=ns.name)
    if ns.state:
        rows = [r for r in rows if r.get("JobRunState") == ns.state]
    print(render_rows(rows, ["Id", "JobRunState", "StartedOn", "ExecutionTime", "ErrorMessage"], ns.output))
    return 0


def cmd_stop(ctx, ns) -> int:
    resp = call(ctx.client("glue").batch_stop_job_run, JobName=ns.name, JobRunIds=ns.run_id)
    errors = resp.get("Errors", [])
    for err in errors:
        eprint(f"could not stop {err.get('JobRunId')}: {err.get('ErrorDetail', {}).get('ErrorMessage')}")
    print(f"requested stop for {len(ns.run_id) - len(errors)} run(s)")
    return 1 if errors else 0


def cmd_logs(ctx, ns) -> int:
    logs = ctx.client("logs")
    group = "/aws-glue/jobs/error" if ns.error else "/aws-glue/jobs/output"
    resp = call(
        logs.get_log_events,
        logGroupName=group, logStreamName=ns.run_id, startFromHead=True, limit=ns.limit,
    )
    for event in resp.get("events", []):
        print(f"{_fmt_ts(event.get('timestamp'))}  {(event.get('message') or '').rstrip(chr(10))}")
    return 0


def cmd_bookmark_reset(ctx, ns) -> int:
    call(ctx.client("glue").reset_job_bookmark, JobName=ns.name)
    print(f"reset bookmark for job {ns.name}")
    return 0
