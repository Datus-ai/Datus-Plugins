"""Read current DAG metadata and Python source through MWAA's Airflow API."""

from __future__ import annotations

import argparse
import sys
from urllib.parse import quote

from datus_aws_common import PluginError, UsageError, add_output_option, render_rows

from ..airflow_client import MwaaAirflowClient


def register(sub: argparse._SubParsersAction) -> None:
    dags = sub.add_parser("dags", help="read current DAGs from the MWAA Airflow API")
    group = dags.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    parser = group.add_parser("list", help="list current active, non-stale DAGs")
    parser.add_argument("--env", help="environment (default: profile's environment)")
    parser.add_argument("-p", "--pattern", help="filter dag_id with a SQL LIKE pattern")
    paused = parser.add_mutually_exclusive_group()
    paused.add_argument("--paused", action="store_true", help="only paused DAGs")
    paused.add_argument("--unpaused", action="store_true", help="only unpaused DAGs")
    parser.add_argument("--limit", type=int, help="maximum DAGs to return (default: all)")
    add_output_option(parser)
    parser.set_defaults(func=cmd_list)

    parser = group.add_parser("source", help="print the current Python source of a DAG")
    parser.add_argument("dag_id")
    parser.add_argument("--env", help="environment (default: profile's environment)")
    parser.set_defaults(func=cmd_source)


def _environment(ctx, explicit: str | None) -> str:
    environment = explicit or ctx.settings.environment
    if not environment:
        raise UsageError("no environment (--env or config environment)")
    return environment


def _client(ctx, environment: str) -> MwaaAirflowClient:
    return MwaaAirflowClient(
        ctx.client("mwaa"),
        environment,
        timeout=ctx.settings.aws.timeout,
    )


def cmd_list(ctx, ns) -> int:
    params = {"only_active": "true", "dag_id_pattern": ns.pattern}
    if ns.paused:
        params["paused"] = "true"
    elif ns.unpaused:
        params["paused"] = "false"
    rows = _client(ctx, _environment(ctx, ns.env)).paginate(
        "/dags", "dags", params=params, limit=ns.limit
    )
    print(render_rows(rows, ["dag_id", "fileloc", "owners", "is_paused"], ns.output))
    return 0


def cmd_source(ctx, ns) -> int:
    client = _client(ctx, _environment(ctx, ns.env))
    dag_id = quote(ns.dag_id, safe="")
    dag = client.request("GET", f"/dags/{dag_id}")
    file_token = (dag or {}).get("file_token")
    if not file_token:
        raise PluginError(f"MWAA Airflow returned no file_token for DAG {ns.dag_id!r}")
    text = client.request(
        "GET", f"/dagSources/{quote(str(file_token), safe='')}", accept="text/plain"
    )
    if isinstance(text, dict):
        text = text.get("content", "")
    text = str(text or "")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0
