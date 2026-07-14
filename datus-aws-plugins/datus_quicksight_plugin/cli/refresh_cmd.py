"""`datus quicksight refresh ...` — SPICE ingestions (run [--wait]) and refresh schedules."""

from __future__ import annotations

import argparse
import time

from datus_aws_common import (
    PluginError,
    add_output_option,
    call,
    confirm,
    eprint,
    paginate,
    parse_json_arg,
    render_one,
    render_rows,
    wait_until,
)

from ._helpers import acct, qs

TERMINAL_INGESTION = {"COMPLETED", "FAILED", "CANCELLED"}


def register(sub: argparse._SubParsersAction) -> None:
    refresh = sub.add_parser("refresh", help="SPICE ingestions and refresh schedules")
    group = refresh.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list ingestions of a dataset")
    p.add_argument("dataset_id")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("status", help="status of one ingestion")
    p.add_argument("dataset_id")
    p.add_argument("ingestion_id")
    add_output_option(p)
    p.set_defaults(func=cmd_status)

    p = group.add_parser("run", help="start a SPICE ingestion (refresh; billed)")
    p.add_argument("dataset_id")
    p.add_argument("--ingestion-id", help="id for the ingestion (default: datus-<epoch>)")
    p.add_argument("--wait", action="store_true", help="poll until the ingestion is terminal")
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=1800.0)
    p.set_defaults(func=cmd_run)

    p = group.add_parser("cancel", help="cancel a running ingestion")
    p.add_argument("dataset_id")
    p.add_argument("ingestion_id")
    p.set_defaults(func=cmd_cancel)

    p = group.add_parser("schedules", help="list a dataset's refresh schedules")
    p.add_argument("dataset_id")
    add_output_option(p)
    p.set_defaults(func=cmd_schedules)

    p = group.add_parser("schedule-put", help="create/replace a refresh schedule from a JSON body")
    p.add_argument("dataset_id")
    p.add_argument("--cli-input", required=True, help="Schedule object as JSON (the CreateRefreshSchedule 'Schedule')")
    p.set_defaults(func=cmd_schedule_put)

    p = group.add_parser("schedule-delete", help="delete a refresh schedule")
    p.add_argument("dataset_id")
    p.add_argument("schedule_id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_schedule_delete)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_ingestions", "Ingestions", limit=ns.limit, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id)
    print(render_rows(rows, ["IngestionId", "IngestionStatus", "CreatedTime", "RowInfo"], ns.output))
    return 0


def _describe(ctx, dataset_id, ingestion_id):
    return call(qs(ctx).describe_ingestion, AwsAccountId=acct(ctx), DataSetId=dataset_id, IngestionId=ingestion_id)["Ingestion"]


def cmd_status(ctx, ns) -> int:
    print(render_one(_describe(ctx, ns.dataset_id, ns.ingestion_id), ns.output))
    return 0


def cmd_run(ctx, ns) -> int:
    ingestion_id = ns.ingestion_id or f"datus-{int(time.time())}"
    resp = call(qs(ctx).create_ingestion, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id, IngestionId=ingestion_id)
    print(f"started ingestion {ingestion_id}: {resp.get('IngestionStatus')}")
    if not ns.wait:
        return 0
    final = wait_until(
        lambda: _describe(ctx, ns.dataset_id, ingestion_id),
        lambda ing: ing.get("IngestionStatus") in TERMINAL_INGESTION,
        timeout=ns.timeout, interval=ns.interval,
        on_change=lambda ing: eprint(f"ingestion {ingestion_id}: {ing.get('IngestionStatus')}"),
    )
    status = final.get("IngestionStatus")
    print(f"ingestion {ingestion_id}: {status}")
    if status != "COMPLETED" and final.get("ErrorInfo"):
        eprint(str(final["ErrorInfo"]))
    return 0 if status == "COMPLETED" else 1


def cmd_cancel(ctx, ns) -> int:
    call(qs(ctx).cancel_ingestion, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id, IngestionId=ns.ingestion_id)
    print(f"cancelled ingestion {ns.ingestion_id}")
    return 0


def cmd_schedules(ctx, ns) -> int:
    resp = call(qs(ctx).list_refresh_schedules, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id)
    print(render_rows(resp.get("RefreshSchedules", []), ["ScheduleId", "RefreshType", "StartAfterDateTime"], ns.output))
    return 0


def cmd_schedule_put(ctx, ns) -> int:
    schedule = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_refresh_schedule, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id, Schedule=schedule)
    print(f"put refresh schedule {resp.get('ScheduleId', schedule.get('ScheduleId'))}")
    return 0


def cmd_schedule_delete(ctx, ns) -> int:
    if not confirm(f"delete refresh schedule {ns.schedule_id} of dataset {ns.dataset_id}?", ns.yes):
        print("aborted")
        return 1
    call(qs(ctx).delete_refresh_schedule, AwsAccountId=acct(ctx), DataSetId=ns.dataset_id, ScheduleId=ns.schedule_id)
    print(f"deleted refresh schedule {ns.schedule_id}")
    return 0
