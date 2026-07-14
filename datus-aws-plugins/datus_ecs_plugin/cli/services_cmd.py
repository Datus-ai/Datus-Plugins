"""`datus ecs services ...` — list/describe services, view events, scale."""

from __future__ import annotations

import argparse

from datus_aws_common import PluginError, add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    services = sub.add_parser("services", help="ECS services: list, describe, events, scale")
    group = services.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list services in a cluster")
    p.add_argument("cluster")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one service")
    p.add_argument("cluster")
    p.add_argument("service")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("events", help="recent service events")
    p.add_argument("cluster")
    p.add_argument("service")
    p.add_argument("--limit", type=int, default=20)
    add_output_option(p)
    p.set_defaults(func=cmd_events)

    p = group.add_parser("scale", help="set a service's desired task count")
    p.add_argument("cluster")
    p.add_argument("service")
    p.add_argument("count", type=int)
    p.set_defaults(func=cmd_scale)


def cmd_list(ctx, ns) -> int:
    client = ctx.client("ecs")
    arns = paginate(client, "list_services", "serviceArns", limit=ns.limit, cluster=ns.cluster)
    if not arns:
        print(render_rows([], ["serviceName", "status"], ns.output))
        return 0
    rows = call(client.describe_services, cluster=ns.cluster, services=arns[:10]).get("services", [])
    print(render_rows(rows, ["serviceName", "status", "desiredCount", "runningCount", "pendingCount"], ns.output))
    return 0


def _one_service(ctx, cluster, service):
    resp = call(ctx.client("ecs").describe_services, cluster=cluster, services=[service])
    services = resp.get("services", [])
    if not services:
        raise PluginError(f"service not found: {service}")
    return services[0]


def cmd_describe(ctx, ns) -> int:
    print(render_one(_one_service(ctx, ns.cluster, ns.service), ns.output))
    return 0


def cmd_events(ctx, ns) -> int:
    events = _one_service(ctx, ns.cluster, ns.service).get("events", [])[: ns.limit]
    print(render_rows(events, ["createdAt", "message"], ns.output))
    return 0


def cmd_scale(ctx, ns) -> int:
    call(ctx.client("ecs").update_service, cluster=ns.cluster, service=ns.service, desiredCount=ns.count)
    print(f"scaled {ns.service} -> {ns.count}")
    return 0
