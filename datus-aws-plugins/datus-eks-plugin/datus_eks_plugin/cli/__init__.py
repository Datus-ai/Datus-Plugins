"""Read-only command surface for `datus eks`."""

from __future__ import annotations

import argparse
import json
from typing import Any

from datus_aws_common import (
    add_output_option,
    call,
    paginate,
    render_one,
    render_rows,
    run,
)

from ..client import EksContext
from ..config import Settings

PROG = "datus eks"


def _group(sub: argparse._SubParsersAction, name: str, help_text: str):
    parser = sub.add_parser(name, help=help_text)
    return parser.add_subparsers(dest=f"{name}_command", required=True, metavar="<command>")


def _resource_group(
    sub: argparse._SubParsersAction,
    *,
    name: str,
    noun: str,
    list_handler,
    describe_handler,
) -> None:
    group = _group(sub, name, f"list and describe EKS {noun}")
    parser = group.add_parser("list", help=f"list {noun}")
    parser.add_argument("--limit", type=int)
    add_output_option(parser)
    parser.set_defaults(func=list_handler)
    parser = group.add_parser("describe", help=f"describe one {noun.rstrip('s')}")
    parser.add_argument("name")
    add_output_option(parser)
    parser.set_defaults(func=describe_handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Inspect Amazon EKS and authenticate Kubernetes without the AWS CLI.",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    group = _group(sub, "clusters", "list and describe EKS clusters")
    command = group.add_parser("list", help="list clusters in the configured region")
    command.add_argument("--limit", type=int)
    add_output_option(command)
    command.set_defaults(func=_clusters_list)
    command = group.add_parser("describe", help="describe the configured cluster")
    add_output_option(command)
    command.set_defaults(func=_clusters_describe)

    _resource_group(
        sub,
        name="nodegroups",
        noun="managed node groups",
        list_handler=_nodegroups_list,
        describe_handler=_nodegroups_describe,
    )
    _resource_group(
        sub,
        name="addons",
        noun="add-ons",
        list_handler=_addons_list,
        describe_handler=_addons_describe,
    )
    _resource_group(
        sub,
        name="access-entries",
        noun="access entries",
        list_handler=_access_entries_list,
        describe_handler=_access_entries_describe,
    )
    _resource_group(
        sub,
        name="fargate-profiles",
        noun="Fargate profiles",
        list_handler=_fargate_profiles_list,
        describe_handler=_fargate_profiles_describe,
    )

    group = _group(sub, "updates", "list and describe EKS updates")
    command = group.add_parser("list", help="list updates for the configured cluster")
    _update_scope_args(command)
    command.add_argument("--limit", type=int)
    add_output_option(command)
    command.set_defaults(func=_updates_list)
    command = group.add_parser("describe", help="describe an update")
    command.add_argument("id")
    _update_scope_args(command)
    add_output_option(command)
    command.set_defaults(func=_updates_describe)

    group = _group(sub, "insights", "list and describe EKS upgrade insights")
    command = group.add_parser("list", help="list upgrade insights")
    command.add_argument("--category", action="append")
    command.add_argument("--status", action="append")
    command.add_argument("--kubernetes-version", action="append")
    command.add_argument("--limit", type=int)
    add_output_option(command)
    command.set_defaults(func=_insights_list)
    command = group.add_parser("describe", help="describe one upgrade insight")
    command.add_argument("id")
    add_output_option(command)
    command.set_defaults(func=_insights_describe)

    group = _group(sub, "auth", "inspect the active AWS identity")
    command = group.add_parser("whoami", help="show STS caller identity")
    add_output_option(command)
    command.set_defaults(func=_auth_whoami)

    group = _group(sub, "kubernetes", "machine-facing k8s provider protocol")
    command = group.add_parser("cluster", help="emit cluster connection JSON")
    command.set_defaults(func=_kubernetes_cluster)
    command = group.add_parser("credential", help="emit ExecCredential JSON")
    command.set_defaults(func=_kubernetes_credential)
    return parser


def _update_scope_args(parser: argparse.ArgumentParser) -> None:
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--nodegroup")
    scope.add_argument("--addon")


def _print_names(values: list[str], label: str, output: str) -> None:
    print(render_rows([{label: value} for value in values], [label], output))


def _cluster(ctx: EksContext) -> str:
    return ctx.settings.cluster


def _clusters_list(ctx: EksContext, ns: argparse.Namespace) -> int:
    values = paginate(ctx.client("eks"), "list_clusters", "clusters", limit=ns.limit)
    _print_names(values, "cluster", ns.output)
    return 0


def _clusters_describe(ctx: EksContext, ns: argparse.Namespace) -> int:
    value = call(ctx.client("eks").describe_cluster, name=_cluster(ctx)).get("cluster") or {}
    print(render_one(value, ns.output))
    return 0


def _nodegroups_list(ctx: EksContext, ns: argparse.Namespace) -> int:
    values = paginate(
        ctx.client("eks"), "list_nodegroups", "nodegroups", limit=ns.limit,
        clusterName=_cluster(ctx),
    )
    _print_names(values, "nodegroup", ns.output)
    return 0


def _nodegroups_describe(ctx: EksContext, ns: argparse.Namespace) -> int:
    value = call(
        ctx.client("eks").describe_nodegroup,
        clusterName=_cluster(ctx), nodegroupName=ns.name,
    ).get("nodegroup") or {}
    print(render_one(value, ns.output))
    return 0


def _addons_list(ctx: EksContext, ns: argparse.Namespace) -> int:
    values = paginate(
        ctx.client("eks"), "list_addons", "addons", limit=ns.limit,
        clusterName=_cluster(ctx),
    )
    _print_names(values, "addon", ns.output)
    return 0


def _addons_describe(ctx: EksContext, ns: argparse.Namespace) -> int:
    value = call(
        ctx.client("eks").describe_addon,
        clusterName=_cluster(ctx), addonName=ns.name,
    ).get("addon") or {}
    print(render_one(value, ns.output))
    return 0


def _access_entries_list(ctx: EksContext, ns: argparse.Namespace) -> int:
    values = paginate(
        ctx.client("eks"), "list_access_entries", "accessEntries", limit=ns.limit,
        clusterName=_cluster(ctx),
    )
    _print_names(values, "principalArn", ns.output)
    return 0


def _access_entries_describe(ctx: EksContext, ns: argparse.Namespace) -> int:
    value = call(
        ctx.client("eks").describe_access_entry,
        clusterName=_cluster(ctx), principalArn=ns.name,
    ).get("accessEntry") or {}
    print(render_one(value, ns.output))
    return 0


def _fargate_profiles_list(ctx: EksContext, ns: argparse.Namespace) -> int:
    values = paginate(
        ctx.client("eks"), "list_fargate_profiles", "fargateProfileNames",
        limit=ns.limit, clusterName=_cluster(ctx),
    )
    _print_names(values, "fargateProfile", ns.output)
    return 0


def _fargate_profiles_describe(ctx: EksContext, ns: argparse.Namespace) -> int:
    value = call(
        ctx.client("eks").describe_fargate_profile,
        clusterName=_cluster(ctx), fargateProfileName=ns.name,
    ).get("fargateProfile") or {}
    print(render_one(value, ns.output))
    return 0


def _update_kwargs(ctx: EksContext, ns: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"name": _cluster(ctx)}
    if ns.nodegroup:
        kwargs["nodegroupName"] = ns.nodegroup
    if ns.addon:
        kwargs["addonName"] = ns.addon
    return kwargs


def _updates_list(ctx: EksContext, ns: argparse.Namespace) -> int:
    values = paginate(
        ctx.client("eks"), "list_updates", "updateIds", limit=ns.limit,
        **_update_kwargs(ctx, ns),
    )
    _print_names(values, "updateId", ns.output)
    return 0


def _updates_describe(ctx: EksContext, ns: argparse.Namespace) -> int:
    value = call(
        ctx.client("eks").describe_update,
        updateId=ns.id,
        **_update_kwargs(ctx, ns),
    ).get("update") or {}
    print(render_one(value, ns.output))
    return 0


def _insights_list(ctx: EksContext, ns: argparse.Namespace) -> int:
    filters = {}
    if ns.category:
        filters["categories"] = ns.category
    if ns.status:
        filters["statuses"] = ns.status
    if ns.kubernetes_version:
        filters["kubernetesVersions"] = ns.kubernetes_version
    kwargs: dict[str, Any] = {"clusterName": _cluster(ctx)}
    if filters:
        kwargs["filter"] = filters
    rows = paginate(
        ctx.client("eks"), "list_insights", "insights", limit=ns.limit, **kwargs
    )
    print(render_rows(rows, ["id", "name", "category", "kubernetesVersion", "lastRefreshTime", "status"], ns.output))
    return 0


def _insights_describe(ctx: EksContext, ns: argparse.Namespace) -> int:
    value = call(
        ctx.client("eks").describe_insight,
        clusterName=_cluster(ctx), id=ns.id,
    ).get("insight") or {}
    print(render_one(value, ns.output))
    return 0


def _auth_whoami(ctx: EksContext, ns: argparse.Namespace) -> int:
    response = call(ctx.client("sts").get_caller_identity)
    value = {
        "Account": response.get("Account"),
        "UserId": response.get("UserId"),
        "Arn": response.get("Arn"),
    }
    print(render_one(value, ns.output))
    return 0


def _kubernetes_cluster(ctx: EksContext, _ns: argparse.Namespace) -> int:
    print(json.dumps(ctx.cluster_connection().to_dict(), separators=(",", ":")))
    return 0


def _kubernetes_credential(ctx: EksContext, _ns: argparse.Namespace) -> int:
    print(json.dumps(ctx.exec_credential().to_dict(), separators=(",", ":")))
    return 0


def main(argv: list[str], profile: dict[str, Any]) -> int:
    return run(build_parser(), argv, lambda: EksContext(Settings.from_profile(profile)))
