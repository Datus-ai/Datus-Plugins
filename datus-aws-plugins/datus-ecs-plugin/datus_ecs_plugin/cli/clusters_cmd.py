"""`datus ecs clusters ...` — list and describe clusters."""

from __future__ import annotations

import argparse

from datus_aws_common import PluginError, add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    clusters = sub.add_parser("clusters", help="ECS clusters: list, describe")
    group = clusters.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list clusters")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one cluster")
    p.add_argument("cluster")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)


def cmd_list(ctx, ns) -> int:
    client = ctx.client("ecs")
    arns = paginate(client, "list_clusters", "clusterArns")
    if not arns:
        print(render_rows([], ["clusterName", "status"], ns.output))
        return 0
    rows = call(client.describe_clusters, clusters=arns).get("clusters", [])
    print(render_rows(rows, ["clusterName", "status", "runningTasksCount", "activeServicesCount"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    resp = call(ctx.client("ecs").describe_clusters, clusters=[ns.cluster])
    clusters = resp.get("clusters", [])
    if not clusters:
        raise PluginError(f"cluster not found: {ns.cluster}")
    print(render_one(clusters[0], ns.output))
    return 0
