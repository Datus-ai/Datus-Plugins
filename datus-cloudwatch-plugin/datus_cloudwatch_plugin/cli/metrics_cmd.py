"""`datus cloudwatch metrics ...` — list metrics and fetch statistics."""

from __future__ import annotations

import argparse

from datus_aws_common import (
    UsageError,
    add_output_option,
    call,
    paginate,
    parse_datetime_arg,
    render_rows,
)

STATS = ["Average", "Sum", "Minimum", "Maximum", "SampleCount"]


def register(sub: argparse._SubParsersAction) -> None:
    metrics = sub.add_parser("metrics", help="CloudWatch metrics: list, get")
    group = metrics.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list available metrics")
    p.add_argument("--namespace", help="e.g. AWS/Lambda, AWS/Glue")
    p.add_argument("--name", help="metric name")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="get metric statistics over a time range")
    p.add_argument("--namespace", required=True)
    p.add_argument("--name", required=True, help="metric name")
    p.add_argument("--stat", choices=STATS, default="Average")
    p.add_argument("--start", required=True, help="ISO 8601 or 'now'")
    p.add_argument("--end", default="now", help="ISO 8601 or 'now' (default now)")
    p.add_argument("--period", type=int, default=300, help="aggregation period in seconds (default 300)")
    p.add_argument("-d", "--dimension", action="append", dest="dimensions", metavar="Name=Value",
                   help="metric dimension (repeatable)")
    add_output_option(p)
    p.set_defaults(func=cmd_get)


def _parse_dimensions(items):
    dims = []
    for raw in items or []:
        if "=" not in raw:
            raise UsageError(f"--dimension must be Name=Value (got {raw!r})")
        name, value = raw.split("=", 1)
        dims.append({"Name": name, "Value": value})
    return dims


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def cmd_list(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    kwargs = {}
    if ns.namespace:
        kwargs["Namespace"] = ns.namespace
    if ns.name:
        kwargs["MetricName"] = ns.name
    rows = paginate(client, "list_metrics", "Metrics", limit=ns.limit, **kwargs)
    print(render_rows(rows, ["Namespace", "MetricName", "Dimensions"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    client = ctx.client("cloudwatch")
    resp = call(
        client.get_metric_statistics,
        Namespace=ns.namespace,
        MetricName=ns.name,
        StartTime=parse_datetime_arg(ns.start, "--start"),
        EndTime=parse_datetime_arg(ns.end, "--end"),
        Period=ns.period,
        Statistics=[ns.stat],
        Dimensions=_parse_dimensions(ns.dimensions),
    )
    points = sorted(resp.get("Datapoints", []), key=lambda d: d.get("Timestamp"))
    if ns.output in ("json", "yaml"):
        print(render_rows(points, None, ns.output))
        return 0
    rows = [{"Timestamp": _iso(d.get("Timestamp")), ns.stat: d.get(ns.stat), "Unit": d.get("Unit")} for d in points]
    print(render_rows(rows, ["Timestamp", ns.stat, "Unit"], ns.output))
    return 0
