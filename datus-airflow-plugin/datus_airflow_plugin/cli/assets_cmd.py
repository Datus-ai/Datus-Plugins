"""`datus airflow assets ...` — data assets (Airflow 3 replacement for datasets)."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from ..errors import UsageError
from ..output import render_one, render_rows
from . import Context, add_output_option


def register(sub: argparse._SubParsersAction) -> None:
    assets = sub.add_parser("assets", help="inspect and materialize data assets")
    group = assets.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list assets")
    p.add_argument("--name-pattern", help="filter by name substring")
    p.add_argument("--uri-pattern", help="filter by uri substring")
    p.add_argument("--limit", type=int, help="stop after N assets (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("details", help="show one asset")
    _add_selector(p)
    add_output_option(p)
    p.set_defaults(func=cmd_details)

    p = group.add_parser(
        "materialize", help="materialize an asset (trigger the DAG that produces it)"
    )
    _add_selector(p)
    add_output_option(p)
    p.set_defaults(func=cmd_materialize)

    p = group.add_parser("events", help="list asset events")
    p.add_argument("--asset-id", type=int)
    p.add_argument("--source-dag-id")
    p.add_argument("--limit", type=int, help="stop after N events (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_events)


def _add_selector(p: argparse.ArgumentParser) -> None:
    p.add_argument("--id", type=int, help="asset id")
    p.add_argument("--name", help="exact asset name")
    p.add_argument("--uri", help="exact asset uri")


def _resolve_asset_id(ctx: Context, ns) -> int:
    if ns.id is not None:
        return ns.id
    if not ns.name and not ns.uri:
        raise UsageError("select the asset with --id, --name or --uri")
    params: Dict[str, Any] = {}
    if ns.name:
        params["name_pattern"] = ns.name
    if ns.uri:
        params["uri_pattern"] = ns.uri
    candidates: List[Dict[str, Any]] = ctx.client.paginate("/assets", "assets", params=params)
    exact = [
        a
        for a in candidates
        if (not ns.name or a.get("name") == ns.name) and (not ns.uri or a.get("uri") == ns.uri)
    ]
    if not exact:
        raise UsageError("no asset matches the given --name/--uri")
    if len(exact) > 1:
        names = ", ".join(f"{a.get('id')}:{a.get('name')}" for a in exact[:10])
        raise UsageError(f"multiple assets match ({names}); disambiguate with --id")
    return int(exact[0]["id"])


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    params = {"name_pattern": ns.name_pattern, "uri_pattern": ns.uri_pattern}
    rows = ctx.client.paginate("/assets", "assets", params=params, limit=ns.limit)
    print(render_rows(rows, ["id", "name", "uri", "group"], ns.output))
    return 0


def cmd_details(ctx: Context, ns) -> int:
    asset_id = _resolve_asset_id(ctx, ns)
    data = ctx.client.request("GET", f"/assets/{asset_id}")
    print(render_one(data, ns.output))
    return 0


def cmd_materialize(ctx: Context, ns) -> int:
    asset_id = _resolve_asset_id(ctx, ns)
    run = ctx.client.request("POST", f"/assets/{asset_id}/materialize")
    print(render_one(run, ns.output))
    return 0


def cmd_events(ctx: Context, ns) -> int:
    params = {"asset_id": ns.asset_id, "source_dag_id": ns.source_dag_id}
    rows = ctx.client.paginate("/assets/events", "asset_events", params=params, limit=ns.limit)
    columns = ["asset_id", "source_dag_id", "source_task_id", "source_run_id", "timestamp"]
    print(render_rows(rows, columns, ns.output))
    return 0
