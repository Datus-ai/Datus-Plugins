"""`datus cloudwatch alarms ...` — list, get, history, set-state."""

from __future__ import annotations

import argparse

from datus_aws_common import (
    PluginError,
    add_output_option,
    call,
    paginate,
    render_one,
    render_rows,
)

ALARM_STATES = ["OK", "ALARM", "INSUFFICIENT_DATA"]


def register(sub: argparse._SubParsersAction) -> None:
    alarms = sub.add_parser("alarms", help="CloudWatch alarms: list, get, history, set-state")
    group = alarms.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list metric alarms")
    p.add_argument("-p", "--prefix", help="alarm name prefix")
    p.add_argument("--state", choices=ALARM_STATES, help="filter by current state")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one alarm")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("history", help="alarm state-change history")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=50)
    add_output_option(p)
    p.set_defaults(func=cmd_history)

    p = group.add_parser("set-state", help="set an alarm's state (testing / temporary suppression)")
    p.add_argument("name")
    p.add_argument("--state", choices=ALARM_STATES, required=True)
    p.add_argument("--reason", default="set via datus cloudwatch")
    p.set_defaults(func=cmd_set_state)


def cmd_list(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    kwargs = {}
    if ns.prefix:
        kwargs["AlarmNamePrefix"] = ns.prefix
    if ns.state:
        kwargs["StateValue"] = ns.state
    rows = paginate(client, "describe_alarms", "MetricAlarms", limit=ns.limit, **kwargs)
    print(render_rows(rows, ["AlarmName", "StateValue", "MetricName", "Namespace", "ComparisonOperator"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    resp = call(client.describe_alarms, AlarmNames=[ns.name])
    alarms = resp.get("MetricAlarms", []) + resp.get("CompositeAlarms", [])
    if not alarms:
        raise PluginError(f"alarm not found: {ns.name}")
    print(render_one(alarms[0], ns.output))
    return 0


def cmd_history(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    rows = paginate(client, "describe_alarm_history", "AlarmHistoryItems", limit=ns.limit, AlarmName=ns.name)
    print(render_rows(rows, ["Timestamp", "AlarmName", "HistoryItemType", "HistorySummary"], ns.output))
    return 0


def cmd_set_state(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    call(client.set_alarm_state, AlarmName=ns.name, StateValue=ns.state, StateReason=ns.reason)
    print(f"set alarm {ns.name} -> {ns.state}")
    return 0
