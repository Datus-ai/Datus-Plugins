from __future__ import annotations

import argparse
import json
from typing import Any

from datus_gcp_common import add_output_option, call, render_one, render_rows, run

from .client import GkeContext, as_dict, field
from .config import Settings


def _group(sub, name, help_text):
    parser = sub.add_parser(name, help=help_text)
    return parser.add_subparsers(dest=f"{name}_command", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datus gke",
        description="Inspect GKE and provide Kubernetes authentication.",
    )
    sub = parser.add_subparsers(dest="group", required=True)

    group = _group(sub, "clusters", "inspect clusters")
    cmd = group.add_parser("list")
    add_output_option(cmd)
    cmd.set_defaults(func=_clusters_list)
    cmd = group.add_parser("describe")
    add_output_option(cmd)
    cmd.set_defaults(func=_clusters_describe)

    group = _group(sub, "nodepools", "inspect node pools")
    cmd = group.add_parser("list")
    add_output_option(cmd)
    cmd.set_defaults(func=_nodepools_list)
    cmd = group.add_parser("describe")
    cmd.add_argument("name")
    add_output_option(cmd)
    cmd.set_defaults(func=_nodepools_describe)

    group = _group(sub, "operations", "inspect cluster operations")
    cmd = group.add_parser("list")
    add_output_option(cmd)
    cmd.set_defaults(func=_operations_list)
    cmd = group.add_parser("describe")
    cmd.add_argument("name")
    add_output_option(cmd)
    cmd.set_defaults(func=_operations_describe)

    group = _group(sub, "server-config", "inspect valid Kubernetes versions")
    cmd = group.add_parser("describe")
    add_output_option(cmd)
    cmd.set_defaults(func=_server_config)

    group = _group(sub, "auth", "validate ADC without printing a token")
    cmd = group.add_parser("check")
    add_output_option(cmd)
    cmd.set_defaults(func=_auth_check)

    group = _group(sub, "kubernetes", "machine-facing k8s provider protocol")
    cmd = group.add_parser("cluster")
    cmd.set_defaults(func=_kubernetes_cluster)
    cmd = group.add_parser("credential")
    cmd.set_defaults(func=_kubernetes_credential)
    return parser


def _clusters_list(ctx: GkeContext, ns) -> int:
    response = call(ctx.client.list_clusters, parent=ctx.settings.parent)
    values = field(response, "clusters", []) or []
    rows = [as_dict(item) for item in values]
    print(
        render_rows(
            rows, ["name", "location", "status", "current_master_version"], ns.output
        )
    )
    return 0


def _clusters_describe(ctx: GkeContext, ns) -> int:
    print(render_one(as_dict(ctx.cluster()), ns.output))
    return 0


def _nodepools_list(ctx: GkeContext, ns) -> int:
    response = call(ctx.client.list_node_pools, parent=ctx.settings.cluster_path)
    rows = [as_dict(item) for item in field(response, "node_pools", []) or []]
    print(
        render_rows(
            rows, ["name", "status", "version", "initial_node_count"], ns.output
        )
    )
    return 0


def _nodepools_describe(ctx: GkeContext, ns) -> int:
    value = call(
        ctx.client.get_node_pool,
        name=f"{ctx.settings.cluster_path}/nodePools/{ns.name}",
    )
    print(render_one(as_dict(value), ns.output))
    return 0


def _operations_list(ctx: GkeContext, ns) -> int:
    response = call(ctx.client.list_operations, parent=ctx.settings.parent)
    rows = [as_dict(item) for item in field(response, "operations", []) or []]
    print(
        render_rows(
            rows, ["name", "operation_type", "status", "target_link"], ns.output
        )
    )
    return 0


def _operations_describe(ctx: GkeContext, ns) -> int:
    name = ns.name if "/" in ns.name else f"{ctx.settings.parent}/operations/{ns.name}"
    print(render_one(as_dict(call(ctx.client.get_operation, name=name)), ns.output))
    return 0


def _server_config(ctx: GkeContext, ns) -> int:
    value = call(ctx.client.get_server_config, name=ctx.settings.parent)
    print(render_one(as_dict(value), ns.output))
    return 0


def _auth_check(ctx: GkeContext, ns) -> int:
    credential = ctx.exec_credential()
    print(
        render_one(
            {"authenticated": True, "expires_at": credential.expiration_timestamp},
            ns.output,
        )
    )
    return 0


def _kubernetes_cluster(ctx: GkeContext, _ns) -> int:
    print(json.dumps(ctx.cluster_connection().to_dict(), separators=(",", ":")))
    return 0


def _kubernetes_credential(ctx: GkeContext, _ns) -> int:
    print(json.dumps(ctx.exec_credential().to_dict(), separators=(",", ":")))
    return 0


def main(argv: list[str], profile: dict[str, Any]) -> int:
    return run(build_parser(), argv, lambda: GkeContext(Settings.from_profile(profile)))
