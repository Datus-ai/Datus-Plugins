from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import yaml

from .client import AckContext, as_dict
from .config import Settings
from .errors import PluginError


def add_output(parser):
    parser.add_argument(
        "-o", "--output", choices=["table", "json", "yaml", "plain"], default="table"
    )


def render(value: Any, output: str) -> str:
    value = as_dict(value)
    if output == "json":
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    if output == "yaml":
        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
    if isinstance(value, list):
        return "\n".join(
            json.dumps(item, ensure_ascii=False, default=str) for item in value
        )
    if isinstance(value, dict):
        return "\n".join(f"{key}: {val}" for key, val in value.items())
    return str(value)


def _group(sub, name):
    return sub.add_parser(name).add_subparsers(dest=f"{name}_command", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datus ack",
        description="Inspect ACK and provide Kubernetes authentication.",
    )
    sub = parser.add_subparsers(dest="group", required=True)
    for group_name, list_fn, describe_fn in (
        ("clusters", _clusters_list, _clusters_describe),
        ("nodepools", _nodepools_list, _nodepools_describe),
        ("addons", _addons_list, _addons_describe),
        ("tasks", _tasks_list, _tasks_describe),
    ):
        group = _group(sub, group_name)
        cmd = group.add_parser("list")
        add_output(cmd)
        cmd.set_defaults(func=list_fn)
        cmd = group.add_parser("describe")
        if group_name not in {"clusters", "addons"}:
            cmd.add_argument("name")
        add_output(cmd)
        cmd.set_defaults(func=describe_fn)
    group = _group(sub, "auth")
    cmd = group.add_parser("check")
    add_output(cmd)
    cmd.set_defaults(func=_auth_check)
    group = _group(sub, "kubernetes")
    group.add_parser("access").set_defaults(func=_kubernetes_access)
    group.add_parser("cluster").set_defaults(func=_kubernetes_cluster)
    group.add_parser("credential").set_defaults(func=_kubernetes_credential)
    return parser


def _clusters_list(ctx, ns):
    request = ctx.request("DescribeClustersV1Request", region_id=ctx.settings.region_id)
    print(render(ctx.invoke("describe_clusters_v1", request), ns.output))
    return 0


def _clusters_describe(ctx, ns):
    print(
        render(
            ctx.invoke("describe_cluster_detail", ctx.settings.cluster_id), ns.output
        )
    )
    return 0


def _nodepools_list(ctx, ns):
    request = ctx.request("DescribeClusterNodePoolsRequest")
    print(
        render(
            ctx.invoke("describe_cluster_node_pools", ctx.settings.cluster_id, request),
            ns.output,
        )
    )
    return 0


def _nodepools_describe(ctx, ns):
    print(
        render(
            ctx.invoke(
                "describe_cluster_node_pool_detail", ctx.settings.cluster_id, ns.name
            ),
            ns.output,
        )
    )
    return 0


def _addons_list(ctx, ns):
    print(
        render(
            ctx.invoke("describe_cluster_addons_version", ctx.settings.cluster_id),
            ns.output,
        )
    )
    return 0


def _addons_describe(ctx, ns):
    request = ctx.request("DescribeClusterAddonsUpgradeStatusRequest")
    print(
        render(
            ctx.invoke(
                "describe_cluster_addons_upgrade_status",
                ctx.settings.cluster_id,
                request,
            ),
            ns.output,
        )
    )
    return 0


def _tasks_list(ctx, ns):
    request = ctx.request("DescribeClusterTasksRequest")
    print(
        render(
            ctx.invoke("describe_cluster_tasks", ctx.settings.cluster_id, request),
            ns.output,
        )
    )
    return 0


def _tasks_describe(ctx, ns):
    print(render(ctx.invoke("describe_task_info", ns.name), ns.output))
    return 0


def _auth_check(ctx, ns):
    value = ctx.exec_credential()
    print(
        render(
            {
                "authenticated": True,
                "credential_type": (
                    "bearer_token" if value.token else "client_certificate"
                ),
                "expires_at": value.expiration_timestamp,
            },
            ns.output,
        )
    )
    return 0


def _kubernetes_cluster(ctx, _ns):
    print(json.dumps(ctx.cluster_connection().to_dict(), separators=(",", ":")))
    return 0


def _kubernetes_access(ctx, _ns):
    print(json.dumps(ctx.kubernetes_access().to_dict(), separators=(",", ":")))
    return 0


def _kubernetes_credential(ctx, _ns):
    print(json.dumps(ctx.exec_credential().to_dict(), separators=(",", ":")))
    return 0


def main(argv: list[str], profile: dict[str, Any]) -> int:
    try:
        ns = build_parser().parse_args(argv)
        return int(ns.func(AckContext(Settings.from_profile(profile)), ns) or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
