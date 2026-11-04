"""Tableau CLI. Remote operations only read; processing output is never executed."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .bindings import read_bindings
from .bundle import inspect_bundle
from .client import TableauClient
from .config import Settings
from .errors import PluginError, UsageError
from .sync import discover_dashboards, sync_sources

GROUPS = {
    "projects": ("list",),
    "workbooks": ("list", "get"),
    "dashboards": ("list", "get"),
    "datasources": ("list", "get"),
    "flows": ("list", "get"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datus tableau",
        description="Synchronize Tableau source definitions for DOSI-driven dashboard rebuilding",
    )
    commands = parser.add_subparsers(dest="group", required=True)
    status = commands.add_parser(
        "status", help="check authentication, API and Metadata availability"
    )
    _output(status)
    for name, operations in GROUPS.items():
        group = commands.add_parser(name)
        subs = group.add_subparsers(dest="command", required=True)
        for operation in operations:
            command = subs.add_parser(operation)
            if operation == "get":
                command.add_argument("id")
            elif name != "projects":
                command.add_argument("--project-id")
            _output(command)
    sync = commands.add_parser(
        "sync", help="source definition snapshots; no ETL execution"
    )
    subs = sync.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run")
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("--dashboard-id", action="append", default=[])
    selection.add_argument("--project-id", action="append", default=[])
    selection.add_argument("--all", action="store_true")
    run.add_argument("--output-root", default="bi_sources/tableau")
    run.add_argument("--overwrite", action="store_true")
    run.add_argument(
        "--external-source",
        action="append",
        default=[],
        help="workspace SQL or JSON definition pack; never executed",
    )
    run.add_argument(
        "--source-bindings",
        help="local JSON mapping full source transform IDs to explicit tables",
    )
    run.add_argument(
        "--dialect",
        default="duckdb",
        help="target SQL dialect for reviewable processing models",
    )
    _output(run)
    inspect = subs.add_parser("inspect")
    inspect.add_argument("bundle_path")
    _output(inspect)
    return parser


def _output(parser):
    parser.add_argument("-o", "--output", choices=("json", "table"), default="json")


def render(value: Any, format: str = "json") -> str:
    if format == "json":
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    rows = value if isinstance(value, list) else [value]
    if not rows:
        return "(no results)"
    if not all(isinstance(row, dict) for row in rows):
        return json.dumps(value, ensure_ascii=False, default=str)
    keys = list(dict.fromkeys(key for row in rows for key in row))

    def cell(value):
        return (
            json.dumps(value, ensure_ascii=False, default=str)
            if isinstance(value, (dict, list))
            else str(value or "")
        )

    cells = [[cell(row.get(key, "")) for key in keys] for row in rows]
    widths = [
        max(len(key), *(len(row[i]) for row in cells)) for i, key in enumerate(keys)
    ]
    return "\n".join(
        "  ".join(item.ljust(widths[i]) for i, item in enumerate(row))
        for row in [keys, *cells]
    )


def main(argv: list[str], profile: dict) -> int:
    try:
        try:
            ns = build_parser().parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        if ns.group == "sync" and ns.command == "inspect":
            result = inspect_bundle(ns.bundle_path)
            print(render(result, ns.output))
            return 0
        with TableauClient(Settings.from_profile(profile)) as client:
            if ns.group == "status":
                result = client.status()
            elif ns.group == "sync":
                result = sync_sources(
                    client,
                    dashboard_ids=ns.dashboard_id,
                    project_ids=ns.project_id,
                    all_dashboards=ns.all,
                    output_root=ns.output_root,
                    overwrite=ns.overwrite,
                    external_sources=ns.external_source,
                    dialect=ns.dialect,
                    source_bindings=read_bindings(ns.source_bindings)
                    if ns.source_bindings
                    else None,
                )
            elif ns.group == "dashboards":
                if ns.command == "get" and (
                    not ns.id.startswith("dashboard:") or len(ns.id.split(":", 2)) != 3
                ):
                    raise UsageError(
                        "use scoped dashboard identifiers returned by dashboards list"
                    )
                rows, books = discover_dashboards(
                    client,
                    workbook_ids={ns.id.split(":", 2)[1]}
                    if ns.command == "get"
                    else None,
                    project_ids=[ns.project_id]
                    if getattr(ns, "project_id", None)
                    else None,
                )
                errors = [item for item in books.values() if "error" in item]
                if errors:
                    raise PluginError(
                        "dashboard inventory is incomplete: one or more workbook definitions could not be downloaded or parsed"
                    )
                if ns.command == "get":
                    matches = [row for row in rows if row["id"] == ns.id]
                    if len(matches) != 1:
                        raise UsageError(
                            "dashboard ID was not found; use dashboards list to discover scoped identifiers"
                        )
                    row = matches[0]
                    result = next(
                        asset
                        for asset in books[row["workbook_id"]]["document"]["assets"]
                        if asset["id"] == ns.id
                    )
                else:
                    result = rows
            elif ns.command == "list":
                result = client.list_resources(
                    ns.group, project_id=getattr(ns, "project_id", None)
                )
            else:
                result = client.get_resource(ns.group, ns.id)
        print(render(result, ns.output))
        # Partial snapshots are saved for inspection, but are never announced as a complete sync.
        return 1 if ns.group == "sync" and result.get("status") != "ok" else 0
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        # SDK errors may include authentication bodies. Keep secrets out of stderr.
        print(
            f"error: Tableau operation failed ({type(exc).__name__}); check service access, permissions and source definitions",
            file=sys.stderr,
        )
        return 1
