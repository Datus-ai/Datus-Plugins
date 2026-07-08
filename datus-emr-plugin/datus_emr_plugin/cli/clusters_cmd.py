"""`datus emr clusters ...` — list/inspect existing clusters and their instances."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, paginate, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    clusters = sub.add_parser("clusters", help="EMR clusters: list, describe, instances")
    group = clusters.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list clusters")
    p.add_argument("--state", action="append", dest="states",
                   help="filter by cluster state (repeatable), e.g. WAITING RUNNING")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one cluster")
    p.add_argument("cluster_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("instances", help="list a cluster's instances")
    p.add_argument("cluster_id")
    add_output_option(p)
    p.set_defaults(func=cmd_instances)


def cmd_list(ctx, ns) -> int:
    kwargs = {}
    if ns.states:
        kwargs["ClusterStates"] = ns.states
    rows = paginate(ctx.client("emr"), "list_clusters", "Clusters", limit=ns.limit, **kwargs)
    if ns.output in ("json", "yaml"):
        print(render_rows(rows, None, ns.output))
        return 0
    view = [{"Id": c.get("Id"), "Name": c.get("Name"), "State": c.get("Status", {}).get("State")} for c in rows]
    print(render_rows(view, ["Id", "Name", "State"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    cluster = call(ctx.client("emr").describe_cluster, ClusterId=ns.cluster_id)["Cluster"]
    print(render_one(cluster, ns.output))
    return 0


def cmd_instances(ctx, ns) -> int:
    rows = paginate(ctx.client("emr"), "list_instances", "Instances", ClusterId=ns.cluster_id)
    if ns.output in ("json", "yaml"):
        print(render_rows(rows, None, ns.output))
        return 0
    view = [
        {"Id": i.get("Id"), "PrivateIpAddress": i.get("PrivateIpAddress"),
         "InstanceType": i.get("InstanceType"), "State": i.get("Status", {}).get("State")}
        for i in rows
    ]
    print(render_rows(view, ["Id", "PrivateIpAddress", "InstanceType", "State"], ns.output))
    return 0
