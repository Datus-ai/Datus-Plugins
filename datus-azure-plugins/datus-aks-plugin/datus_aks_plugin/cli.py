from __future__ import annotations

import argparse
import json
from typing import Any

from datus_azure_common import add_output_option, call, render_one, render_rows, run

from .client import AksContext, as_dict
from .config import Settings


def _group(sub, name, help_text):
    parser = sub.add_parser(name, help=help_text)
    return parser.add_subparsers(dest=f"{name}_command", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datus aks",
        description="Inspect AKS and provide Kubernetes authentication.",
    )
    sub = parser.add_subparsers(dest="group", required=True)
    for name, list_fn, get_fn in (
        ("clusters", _clusters_list, _clusters_describe),
        ("nodepools", _nodepools_list, _nodepools_describe),
        ("maintenance", _maintenance_list, _maintenance_describe),
    ):
        group = _group(sub, name, f"inspect {name}")
        cmd = group.add_parser("list")
        add_output_option(cmd)
        cmd.set_defaults(func=list_fn)
        cmd = group.add_parser("describe")
        if name != "clusters":
            cmd.add_argument("name")
        add_output_option(cmd)
        cmd.set_defaults(func=get_fn)
    group = _group(sub, "upgrades", "inspect available upgrades")
    cmd = group.add_parser("list")
    add_output_option(cmd)
    cmd.set_defaults(func=_upgrades)
    group = _group(sub, "auth", "validate Azure credentials")
    cmd = group.add_parser("check")
    add_output_option(cmd)
    cmd.set_defaults(func=_auth_check)
    group = _group(sub, "kubernetes", "machine-facing k8s provider protocol")
    cmd = group.add_parser("cluster")
    cmd.set_defaults(func=_kubernetes_cluster)
    cmd = group.add_parser("credential")
    cmd.set_defaults(func=_kubernetes_credential)
    return parser


def _clusters_list(ctx: AksContext, ns) -> int:
    rows = [as_dict(item) for item in call(ctx.client.managed_clusters.list)]
    print(
        render_rows(
            rows,
            ["name", "location", "kubernetes_version", "provisioning_state"],
            ns.output,
        )
    )
    return 0


def _clusters_describe(ctx: AksContext, ns) -> int:
    print(render_one(ctx.cluster(), ns.output))
    return 0


def _nodepools_list(ctx: AksContext, ns) -> int:
    values = call(
        ctx.client.agent_pools.list, ctx.settings.resource_group, ctx.settings.cluster
    )
    print(
        render_rows(
            [as_dict(item) for item in values],
            ["name", "count", "vm_size", "orchestrator_version", "provisioning_state"],
            ns.output,
        )
    )
    return 0


def _nodepools_describe(ctx: AksContext, ns) -> int:
    value = call(
        ctx.client.agent_pools.get,
        ctx.settings.resource_group,
        ctx.settings.cluster,
        ns.name,
    )
    print(render_one(value, ns.output))
    return 0


def _maintenance_list(ctx: AksContext, ns) -> int:
    values = call(
        ctx.client.maintenance_configurations.list_by_managed_cluster,
        ctx.settings.resource_group,
        ctx.settings.cluster,
    )
    print(
        render_rows(
            [as_dict(item) for item in values],
            ["name", "maintenance_window", "not_allowed_time"],
            ns.output,
        )
    )
    return 0


def _maintenance_describe(ctx: AksContext, ns) -> int:
    value = call(
        ctx.client.maintenance_configurations.get,
        ctx.settings.resource_group,
        ctx.settings.cluster,
        ns.name,
    )
    print(render_one(value, ns.output))
    return 0


def _upgrades(ctx: AksContext, ns) -> int:
    value = call(
        ctx.client.managed_clusters.get_upgrade_profile,
        ctx.settings.resource_group,
        ctx.settings.cluster,
    )
    print(render_one(value, ns.output))
    return 0


def _auth_check(ctx: AksContext, ns) -> int:
    value = ctx.exec_credential()
    print(
        render_one(
            {"authenticated": True, "expires_at": value.expiration_timestamp}, ns.output
        )
    )
    return 0


def _kubernetes_cluster(ctx: AksContext, _ns) -> int:
    print(json.dumps(ctx.cluster_connection().to_dict(), separators=(",", ":")))
    return 0


def _kubernetes_credential(ctx: AksContext, _ns) -> int:
    print(json.dumps(ctx.exec_credential().to_dict(), separators=(",", ":")))
    return 0


def main(argv: list[str], profile: dict[str, Any]) -> int:
    return run(build_parser(), argv, lambda: AksContext(Settings.from_profile(profile)))
