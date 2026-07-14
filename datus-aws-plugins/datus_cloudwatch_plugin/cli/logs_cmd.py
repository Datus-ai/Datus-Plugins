"""`datus cloudwatch logs ...` — log groups, streams, events, tail, insights."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from datus_aws_common import (
    PluginError,
    UsageError,
    add_output_option,
    call,
    eprint,
    paginate,
    parse_datetime_arg,
    render_rows,
    wait_until,
)

TERMINAL_QUERY_STATES = {"Complete", "Failed", "Cancelled", "Timeout"}
_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def register(sub: argparse._SubParsersAction) -> None:
    logs = sub.add_parser("logs", help="CloudWatch Logs: groups, streams, get, tail, insights")
    group = logs.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("groups", help="list log groups")
    p.add_argument("-p", "--prefix", help="log group name prefix filter")
    p.add_argument("--limit", type=int, help="stop after N groups")
    add_output_option(p)
    p.set_defaults(func=cmd_groups)

    p = group.add_parser("streams", help="list log streams in a group")
    p.add_argument("group_name")
    p.add_argument("--order-by", choices=["LogStreamName", "LastEventTime"], default="LastEventTime")
    p.add_argument("--limit", type=int, default=50)
    add_output_option(p)
    p.set_defaults(func=cmd_streams)

    p = group.add_parser("get", help="get events from one log stream")
    p.add_argument("group_name")
    p.add_argument("stream_name")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--start-from-head", action="store_true", help="oldest first (default: newest)")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("tail", help="tail events across a log group (optionally --follow)")
    p.add_argument("group_name")
    p.add_argument("--filter", dest="filter_pattern", help="CloudWatch Logs filter pattern")
    p.add_argument("--since", default="10m", help="look-back window: 30s/15m/2h/1d (default 10m)")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--follow", action="store_true", help="keep polling for new events until interrupted")
    p.add_argument("--interval", type=float, default=3.0, help="poll interval seconds (with --follow)")
    add_output_option(p)
    p.set_defaults(func=cmd_tail)

    p = group.add_parser("insights", help="run a CloudWatch Logs Insights query and wait for results")
    p.add_argument("group_name", nargs="+", help="one or more log group names")
    p.add_argument("-q", "--query", required=True, help="Logs Insights query string")
    p.add_argument("--start", required=True, help="ISO 8601, 'now', or -1h/-30m/-2d relative")
    p.add_argument("--end", default="now", help="ISO 8601 or 'now' (default now)")
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--timeout", type=float, default=60.0, help="max seconds to wait for results")
    p.add_argument("--interval", type=float, default=1.0)
    add_output_option(p)
    p.set_defaults(func=cmd_insights)


# ------------------------------------------------------------------ helpers


def _fmt_ts(ms) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _since_to_ms(since: str) -> int:
    try:
        secs = int(since[:-1]) * _SINCE_UNITS[since[-1]]
    except (ValueError, KeyError, IndexError):
        raise UsageError(f"--since must look like 30s/15m/2h/1d (got {since!r})")
    return int((time.time() - secs) * 1000)


def _resolve_time(raw: str, what: str) -> datetime:
    """ISO / 'now' via the shared parser, plus -1h/-30m/-2d relative shorthands."""
    if raw and raw[0] == "-" and raw[-1] in _SINCE_UNITS:
        try:
            secs = int(raw[1:-1]) * _SINCE_UNITS[raw[-1]]
        except (ValueError, KeyError):
            raise UsageError(f"{what} relative form must be -30m/-2h/-1d (got {raw!r})")
        return datetime.fromtimestamp(time.time() - secs, tz=timezone.utc)
    return parse_datetime_arg(raw, what)


def _print_events(events, output) -> None:
    if output in ("json", "yaml"):
        print(render_rows(events, None, output))
        return
    for e in events:
        print(f"{_fmt_ts(e.get('timestamp'))}  {(e.get('message') or '').rstrip(chr(10))}")


# ----------------------------------------------------------------- handlers


def cmd_groups(ctx, ns) -> int:
    client = ctx.client("logs")
    kwargs = {}
    if ns.prefix:
        kwargs["logGroupNamePrefix"] = ns.prefix
    rows = paginate(client, "describe_log_groups", "logGroups", limit=ns.limit, **kwargs)
    print(render_rows(rows, ["logGroupName", "storedBytes", "retentionInDays", "metricFilterCount"], ns.output))
    return 0


def cmd_streams(ctx, ns) -> int:
    client = ctx.client("logs")
    rows = paginate(
        client,
        "describe_log_streams",
        "logStreams",
        limit=ns.limit,
        logGroupName=ns.group_name,
        orderBy=ns.order_by,
        descending=(ns.order_by == "LastEventTime"),
    )
    print(render_rows(rows, ["logStreamName", "lastEventTimestamp", "storedBytes"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    client = ctx.client("logs")
    resp = call(
        client.get_log_events,
        logGroupName=ns.group_name,
        logStreamName=ns.stream_name,
        limit=ns.limit,
        startFromHead=ns.start_from_head,
    )
    events = resp.get("events", [])
    if ns.output in ("json", "yaml"):
        print(render_rows(events, None, ns.output))
        return 0
    rows = [{"timestamp": _fmt_ts(e.get("timestamp")), "message": (e.get("message") or "").rstrip("\n")} for e in events]
    print(render_rows(rows, ["timestamp", "message"], ns.output))
    return 0


def cmd_tail(ctx, ns) -> int:
    client = ctx.client("logs")
    base = {"logGroupName": ns.group_name}
    if ns.filter_pattern:
        base["filterPattern"] = ns.filter_pattern

    def fetch(start_ms: int):
        events = []
        token = None
        while True:
            params = dict(base, startTime=start_ms, limit=min(ns.limit, 10000))
            if token:
                params["nextToken"] = token
            resp = call(client.filter_log_events, **params)
            events.extend(resp.get("events", []))
            token = resp.get("nextToken")
            if not token or len(events) >= ns.limit:
                break
        return events[: ns.limit]

    events = fetch(_since_to_ms(ns.since))
    _print_events(events, ns.output)
    if not ns.follow:
        return 0

    last = max((e.get("timestamp", 0) for e in events), default=_since_to_ms(ns.since))
    try:
        while True:
            time.sleep(ns.interval)
            fresh = [e for e in fetch(last + 1) if e.get("timestamp", 0) > last]
            if fresh:
                _print_events(fresh, ns.output)
                last = max(e["timestamp"] for e in fresh)
    except KeyboardInterrupt:
        eprint("interrupted")
        return 0


def cmd_insights(ctx, ns) -> int:
    client = ctx.client("logs")
    start = _resolve_time(ns.start, "--start")
    end = _resolve_time(ns.end, "--end")
    started = call(
        client.start_query,
        logGroupNames=ns.group_name,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=ns.query,
        limit=ns.limit,
    )
    qid = started["queryId"]
    eprint(f"insights query {qid} started")
    final = wait_until(
        lambda: call(client.get_query_results, queryId=qid),
        lambda r: r.get("status") in TERMINAL_QUERY_STATES,
        timeout=ns.timeout,
        interval=ns.interval,
    )
    status = final.get("status")
    if status != "Complete":
        raise PluginError(f"insights query {qid} ended with status {status}")
    rows = [{col["field"]: col["value"] for col in row} for row in final.get("results", [])]
    print(render_rows(rows, None, ns.output))
    return 0
