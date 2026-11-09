"""Tableau CLI. Remote operations only read; processing output is never executed."""

from __future__ import annotations

import argparse
import sys

from .bindings import read_bindings
from .bundle import inspect_bundle
from .client import TableauClient
from .config import Settings
from .errors import PluginError, UsageError
from .output import FORMATS, render
from .sync import discover_dashboards, sync_sources

GROUPS = {
    "projects": ("list",),
    "workbooks": ("list", "get"),
    "dashboards": ("list", "get"),
    "datasources": ("list", "get"),
    "flows": ("list", "get"),
}

# Modules whose exception messages are produced by our own validation, parsing
# or SQL generation. Their messages are descriptive and never embed credentials
# or response bodies, so they are safe to surface verbatim for humans and
# agents. Anything else (network, TLS, the Tableau SDK) may include request or
# response bodies and is reported without echoing its raw message.
_SAFE_ERROR_ORIGINS = ("datus_tableau_plugin", "sqlglot")

# Best-effort classification of masked external failures into an actionable,
# non-sensitive hint keyed by substrings of the originating module name.
_EXTERNAL_ERROR_HINTS = (
    ("ssl", "TLS verification failed; check ca_bundle or verify_ssl"),
    ("certifi", "TLS verification failed; check ca_bundle or verify_ssl"),
    ("socket", "cannot reach server_url; check network and origin"),
    ("urllib3", "cannot reach server_url; check network, origin and TLS"),
    ("requests", "HTTP transport failed; check origin, network and permissions"),
    ("http", "HTTP transport failed; check origin, network and permissions"),
    ("tableauserverclient", "Tableau API call failed; check authentication, site and permissions"),
    ("xml", "could not parse a Tableau response or definition"),
)


def _error_origin_module(exc: BaseException) -> str:
    """Return the module name of the deepest frame where ``exc`` was raised."""

    module = ""
    tb = exc.__traceback__
    while tb is not None:
        module = tb.tb_frame.f_globals.get("__name__", module) or module
        tb = tb.tb_next
    return module


def _describe_unexpected_error(exc: BaseException) -> str:
    """Build an actionable stderr line without leaking credentials.

    Messages raised by our own code or by sqlglot are shown verbatim because
    they describe the actual problem (dialect, formula, parsing) and contain no
    secrets. Errors originating in network/TLS/SDK layers are reported with a
    typed, non-sensitive hint instead of their raw message, which could embed
    authentication bodies.
    """

    etype = type(exc).__name__
    origin = _error_origin_module(exc)
    if origin.startswith(_SAFE_ERROR_ORIGINS):
        message = str(exc).strip() or "no additional detail"
        return f"error: {message} ({etype} in {origin})"
    hint = "check service access, permissions, TLS and source definitions"
    for needle, text in _EXTERNAL_ERROR_HINTS:
        if needle in origin:
            hint = text
            break
    where = origin or "unknown module"
    return (
        f"error: Tableau operation failed ({etype} in {where}); {hint}. "
        "Original message withheld to avoid leaking credentials."
    )


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
    inspect.add_argument(
        "--summary",
        action="store_true",
        help="bounded verified JSON summary; full validation still runs",
    )
    _output(inspect)
    plan = commands.add_parser(
        "plan", help="offline native-flow processing artifacts; no login or execution"
    )
    plan.add_argument("--flow", required=True)
    plan.add_argument("--flow-id", default="local")
    plan.add_argument("--source-bindings")
    plan.add_argument(
        "--output",
        required=True,
        help="new workspace directory for derived models and reports",
    )
    plan.add_argument("--dialect", default="duckdb")
    rebuild = commands.add_parser(
        "rebuild",
        help="offline read-only DOSI binding preflight; prints proposed tool calls",
    )
    rebuild.add_argument("--bundle", required=True)
    rebuild.add_argument("--bindings", required=True)
    rebuild.add_argument("--metric-catalog", required=True)
    serving = commands.add_parser(
        "serving-target", help="show the Datus datasource bound to this Tableau Server"
    )
    _output(serving)
    context = commands.add_parser(
        "context", help="export dashboard sources into project context files"
    )
    context_sub = context.add_subparsers(dest="command", required=True)
    export = context_sub.add_parser(
        "export-dashboard",
        help="snapshot one dashboard's reviewable sources into the project workspace",
    )
    export.add_argument("dashboard_id")
    export.add_argument("--output-root", default="reference_sql")
    export.add_argument("--overwrite", action="store_true")
    export.add_argument(
        "--external-source",
        action="append",
        default=[],
        help="workspace SQL or JSON definition pack; never executed",
    )
    export.add_argument("--source-bindings")
    export.add_argument("--dialect", default="duckdb")
    _output(export)
    api = commands.add_parser(
        "api", help="read-only same-origin escape hatch for untyped REST resources"
    )
    api_sub = api.add_subparsers(dest="command", required=True)
    call = api_sub.add_parser("call")
    call.add_argument("path")
    call.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="query parameter (repeatable)",
    )
    _output(call)
    return parser


def _output(parser):
    parser.add_argument("-o", "--output", choices=FORMATS, default="json")


def _params(values: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise UsageError(f"query parameter must be KEY=VALUE: {value!r}")
        key, item = value.split("=", 1)
        if not key:
            raise UsageError("query parameter key cannot be empty")
        params[key] = item
    return params


def _offline(command, args: list[str]) -> int:
    """Keep standalone parser exits inside the managed plugin's return contract."""
    try:
        return command(args)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def main(argv: list[str], profile: dict) -> int:
    try:
        try:
            ns = build_parser().parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        if ns.group == "plan":
            from .plan import main as plan_main
            from .transforms import normalize_dialect

            try:
                dialect = normalize_dialect(ns.dialect)
            except ValueError as exc:
                raise UsageError(str(exc)) from exc
            args = [
                "--flow",
                ns.flow,
                "--flow-id",
                ns.flow_id,
                "--output",
                ns.output,
                "--dialect",
                dialect,
            ]
            if ns.source_bindings:
                args.extend(["--source-bindings", ns.source_bindings])
            return _offline(plan_main, args)
        if ns.group == "rebuild":
            from .rebuild import main as rebuild_main

            return _offline(
                rebuild_main,
                [
                    "--bundle",
                    ns.bundle,
                    "--bindings",
                    ns.bindings,
                    "--metric-catalog",
                    ns.metric_catalog,
                ],
            )
        if ns.group == "sync" and ns.command == "inspect":
            if ns.summary and ns.output != "json":
                raise UsageError("--summary requires --output json")
            result = (
                inspect_bundle(ns.bundle_path, summary=True)
                if ns.summary
                else inspect_bundle(ns.bundle_path)
            )
            print(render(result, ns.output))
            return 0
        if ns.group == "serving-target":
            settings = Settings.from_profile(profile)
            print(
                render(
                    {
                        "platform": "tableau",
                        "serving_datasource": settings.serving_datasource,
                        "serving_database_name": settings.serving_database_name,
                    },
                    ns.output,
                )
            )
            return 0
        snapshots = (ns.group == "sync" and ns.command == "run") or (
            ns.group == "context" and ns.command == "export-dashboard"
        )
        if snapshots:
            from .transforms import normalize_dialect

            try:
                ns.dialect = normalize_dialect(ns.dialect)
            except ValueError as exc:
                raise UsageError(str(exc)) from exc
        source_bindings = (
            read_bindings(ns.source_bindings)
            if snapshots and ns.source_bindings
            else None
        )
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
                    source_bindings=source_bindings,
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
            elif ns.group == "context":
                result = sync_sources(
                    client,
                    dashboard_ids=[ns.dashboard_id],
                    output_root=f"{ns.output_root}/tableau",
                    overwrite=ns.overwrite,
                    external_sources=ns.external_source,
                    dialect=ns.dialect,
                    source_bindings=source_bindings,
                )
            elif ns.group == "api":
                result = client.rest_get(ns.path, _params(ns.param))
            elif ns.command == "list":
                result = client.list_resources(
                    ns.group, project_id=getattr(ns, "project_id", None)
                )
            else:
                result = client.get_resource(ns.group, ns.id)
        print(render(result, ns.output))
        # Partial snapshots are saved for inspection, but are never announced as a complete sync.
        return (
            1
            if snapshots and isinstance(result, dict) and result.get("status") != "ok"
            else 0
        )
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(_describe_unexpected_error(exc), file=sys.stderr)
        return 1
