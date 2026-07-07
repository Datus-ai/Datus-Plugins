"""Top-level utility commands: version, health, providers, plugins, config, jobs."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from .. import __version__
from ..errors import ApiError, PluginError
from ..output import render_one, render_rows
from . import Context, add_output_option


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("version", help="show server and plugin versions")
    add_output_option(p)
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("health", help="health of metadatabase, scheduler, triggerer, dag processor")
    add_output_option(p)
    p.set_defaults(func=cmd_health)

    providers = sub.add_parser("providers", help="provider packages installed on the server")
    pgroup = providers.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")
    p = pgroup.add_parser("list", help="list providers")
    add_output_option(p)
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("plugins", help="list plugins loaded by the server")
    add_output_option(p)
    p.set_defaults(func=cmd_plugins)

    config = sub.add_parser("config", help="inspect server configuration (requires expose_config)")
    cgroup = config.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")
    p = cgroup.add_parser("list", help="list configuration options")
    p.add_argument("--section", help="only this section")
    add_output_option(p)
    p.set_defaults(func=cmd_config_list)
    p = cgroup.add_parser("get-value", help="print one configuration value")
    p.add_argument("section")
    p.add_argument("option")
    p.set_defaults(func=cmd_config_get_value)

    jobs = sub.add_parser("jobs", help="query server jobs")
    jgroup = jobs.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")
    p = jgroup.add_parser("check", help="exit 0 when matching alive jobs exist, 1 otherwise")
    p.add_argument("--job-type", help="e.g. SchedulerJob, TriggererJob, DagProcessorJob")
    p.add_argument("--hostname", help="only jobs on this host")
    p.add_argument("--limit", type=int, default=1, help="number of recent jobs to check (default: 1)")
    p.add_argument(
        "--allow-multiple", action="store_true", help="do not fail when more than one job is alive"
    )
    add_output_option(p)
    p.set_defaults(func=cmd_jobs_check)


# ---------------------------------------------------------------- handlers


def cmd_version(ctx: Context, ns) -> int:
    data = ctx.client.request("GET", "/version")
    info = {
        "airflow_version": (data or {}).get("version"),
        "git_version": (data or {}).get("git_version"),
        "plugin_version": __version__,
    }
    if ns.output in ("json", "yaml"):
        print(render_one(info, ns.output))
    else:
        print(f"Apache Airflow: {info['airflow_version']} (git: {info['git_version']})")
        print(f"datus-airflow-plugin: {__version__}")
    return 0


def cmd_health(ctx: Context, ns) -> int:
    data = ctx.client.request("GET", "/monitor/health") or {}
    rows: List[Dict[str, Any]] = []
    unhealthy = False
    for component, details in data.items():
        if not isinstance(details, dict):
            continue
        status = details.get("status")
        heartbeat = next(
            (v for k, v in details.items() if k.endswith("heartbeat")),
            None,
        )
        rows.append({"component": component, "status": status, "latest_heartbeat": heartbeat})
        if status not in (None, "healthy"):
            unhealthy = True
    print(render_rows(rows, ["component", "status", "latest_heartbeat"], ns.output))
    return 1 if unhealthy else 0


def cmd_providers(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/providers", "providers")
    print(render_rows(rows, ["package_name", "version", "description"], ns.output))
    return 0


def cmd_plugins(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/plugins", "plugins")
    print(render_rows(rows, ["name", "source"], ns.output))
    return 0


def _config_rows(data: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for section in (data or {}).get("sections", []):
        for option in section.get("options", []):
            value = option.get("value")
            if isinstance(value, (list, tuple)) and len(value) == 2:
                value = value[0]  # (value, source) tuples when the server exposes sources
            rows.append({"section": section.get("name"), "option": option.get("key"), "value": value})
    return rows


def _expose_config_hint(exc: ApiError) -> PluginError:
    return PluginError(
        f"{exc} — the server hides its config by default; set "
        "AIRFLOW__API__EXPOSE_CONFIG=True to enable this endpoint"
    )


def cmd_config_list(ctx: Context, ns) -> int:
    try:
        data = ctx.client.request("GET", "/config", params={"section": ns.section})
    except ApiError as exc:
        if exc.status_code == 403:
            raise _expose_config_hint(exc) from exc
        raise
    print(render_rows(_config_rows(data), ["section", "option", "value"], ns.output))
    return 0


def cmd_config_get_value(ctx: Context, ns) -> int:
    try:
        data = ctx.client.request("GET", f"/config/section/{ns.section}/option/{ns.option}")
    except ApiError as exc:
        if exc.status_code == 403:
            raise _expose_config_hint(exc) from exc
        raise
    rows = _config_rows(data)
    if not rows:
        raise PluginError(f"option [{ns.section}] {ns.option} not found")
    value = rows[0]["value"]
    print(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
    return 0


def cmd_jobs_check(ctx: Context, ns) -> int:
    params = {
        "job_type": ns.job_type,
        "hostname": ns.hostname,
        "is_alive": "true",
        "order_by": "-latest_heartbeat",
    }
    rows = ctx.client.paginate("/jobs", "jobs", params=params, limit=ns.limit)
    columns = ["id", "job_type", "hostname", "state", "latest_heartbeat"]
    print(render_rows(rows, columns, ns.output))
    if not rows:
        print("No alive jobs found.")
        return 1
    if len(rows) > 1 and not ns.allow_multiple:
        print(f"Found {len(rows)} alive jobs; pass --allow-multiple if that is expected.")
        return 1
    print(f"Found {len(rows)} alive job(s).")
    return 0
