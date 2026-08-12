"""CLI entry point for ``datus grafana``."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from .client import GrafanaClient
from .config import Settings
from .errors import PluginError, UsageError
from .exporter import _dashboard_document, export_dashboard
from .operations import OPERATIONS, Operation
from .output import FORMATS, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datus grafana", description="Grafana data-plane CLI")
    groups = parser.add_subparsers(dest="group", required=True, metavar="<command>")
    for group_name, operations in OPERATIONS.items():
        group = groups.add_parser(group_name, help=f"Grafana {group_name} operations")
        subs = group.add_subparsers(dest="subcommand", required=True)
        for name, operation in operations.items():
            command = subs.add_parser(name)
            for argument in operation.args:
                command.add_argument(argument)
            command.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
            if operation.body:
                _add_body_options(command)
            if operation.binary:
                command.add_argument("--output-file", required=True)
            else:
                _add_output(command)
            command.set_defaults(func=_run_operation, operation=operation)

    dashboards = next(action for action in groups._choices_actions if action.dest == "dashboards")
    dashboard_parser = groups.choices[dashboards.dest]
    dashboard_subs = next(action for action in dashboard_parser._actions if isinstance(action, argparse._SubParsersAction))
    for name in ("create", "update", "export"):
        command = dashboard_subs.add_parser(name)
        if name != "create":
            command.add_argument("uid")
        if name in {"create", "update"}:
            _add_body_options(command)
            _add_output(command)
            command.set_defaults(func=_dashboard_write, dashboard_action=name)
        else:
            command.add_argument("--output-file", required=True)
            command.set_defaults(func=_dashboard_export)

    query_parser = groups.choices["queries"]
    query_subs = next(action for action in query_parser._actions if isinstance(action, argparse._SubParsersAction))
    run_panel = query_subs.add_parser("run-panel")
    run_panel.add_argument("dashboard_uid")
    run_panel.add_argument("panel_id")
    run_panel.add_argument("--from", dest="time_from", default="now-6h")
    run_panel.add_argument("--to", dest="time_to", default="now")
    _add_output(run_panel)
    run_panel.set_defaults(func=_run_panel_query)

    panels = groups.add_parser("panels", help="dashboard panel operations")
    panel_subs = panels.add_subparsers(dest="subcommand", required=True)
    for name in ("list", "get", "delete", "query"):
        command = panel_subs.add_parser(name)
        command.add_argument("dashboard_uid")
        if name != "list":
            command.add_argument("panel_id")
        if name == "query":
            command.add_argument("--from", dest="time_from", default="now-6h")
            command.add_argument("--to", dest="time_to", default="now")
        _add_output(command)
        command.set_defaults(func=_panel_command)
    for name in ("create", "update"):
        command = panel_subs.add_parser(name)
        command.add_argument("dashboard_uid")
        if name == "update":
            command.add_argument("panel_id")
        _add_body_options(command)
        _add_output(command)
        command.set_defaults(func=_panel_command)
    copy_panel = panel_subs.add_parser("copy")
    copy_panel.add_argument("source_dashboard_uid")
    copy_panel.add_argument("panel_id")
    copy_panel.add_argument("target_dashboard_uid")
    _add_output(copy_panel)
    copy_panel.set_defaults(func=_panel_command)
    move_panel = panel_subs.add_parser("move")
    move_panel.add_argument("source_dashboard_uid")
    move_panel.add_argument("panel_id")
    move_panel.add_argument("target_dashboard_uid")
    _add_output(move_panel)
    move_panel.set_defaults(func=_panel_command)

    serving = groups.add_parser("serving-target", help="show the datasource bound to this BI instance")
    _add_output(serving)
    serving.set_defaults(func=_serving_target)

    context = groups.add_parser("context", help="export dashboard queries into project context files")
    context_sub = context.add_subparsers(dest="subcommand", required=True)
    export = context_sub.add_parser("export-dashboard")
    export.add_argument("dashboard_uid")
    export.add_argument("--output-root", default="reference_sql")
    export.add_argument("--include-hidden", action="store_true")
    export.add_argument("--overwrite", action="store_true")
    _add_output(export)
    export.set_defaults(func=_export_context)

    api = groups.add_parser("api", help="safe same-origin escape hatch for untyped API resources")
    api_sub = api.add_subparsers(dest="subcommand", required=True)
    call = api_sub.add_parser("call")
    call.add_argument("method", choices=("GET", "POST", "PUT", "PATCH", "DELETE"))
    call.add_argument("path")
    call.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    _add_body_options(call, required=False)
    call.add_argument("--output-file")
    _add_output(call)
    call.set_defaults(func=_api_call)
    return parser


def main(argv: list[str], profile: dict[str, Any]) -> int:
    try:
        ns = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    client: GrafanaClient | None = None
    try:
        settings = Settings.from_profile(profile)
        client = GrafanaClient(settings)
        result = ns.func(client, settings, ns)
        if result is not None:
            print(render(result, getattr(ns, "output", "json")))
        return 0
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        print(f"error: cannot reach Grafana: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: Grafana request failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        if client:
            client.close()


def _run_operation(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> Any:
    operation: Operation = ns.operation
    if ns.group == "dashboards" and ns.subcommand in {"get", "delete"}:
        return client.dashboard_request(operation.method, ns.uid, params=_params(ns.param))
    path = operation.path
    for argument in operation.args:
        path = path.replace("{" + argument + "}", quote(str(getattr(ns, argument)), safe=""))
    return client.request(
        operation.method, path, params=_params(ns.param),
        json_body=_body(ns) if operation.body else None,
    )


def _dashboard_write(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> Any:
    payload = _body(ns)
    return client.dashboard_request("POST" if ns.dashboard_action == "create" else "PUT", getattr(ns, "uid", None), json_body=payload)


def _dashboard_export(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> dict[str, Any]:
    payload = client.dashboard_request("GET", ns.uid)
    destination = _safe_workspace_file(ns.output_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode() + b"\n"
    destination.write_bytes(content)
    return {"output_file": str(destination), "bytes": len(content)}


def _panel_command(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> Any:
    action = ns.subcommand
    if action in {"copy", "move"}:
        source_payload = client.dashboard_request("GET", ns.source_dashboard_uid)
        source_dashboard, _ = _dashboard_document(source_payload)
        panel, container = _find_panel(source_dashboard, ns.panel_id)
        if panel is None or container is None:
            raise UsageError(f"panel {ns.panel_id!r} was not found")
        target_payload = client.dashboard_request("GET", ns.target_dashboard_uid)
        target_dashboard, _ = _dashboard_document(target_payload)
        copied = copy.deepcopy(panel)
        copied["id"] = _next_panel_id(target_dashboard)
        target_dashboard.setdefault("panels", []).append(copied)
        target_result = _save_dashboard(client, ns.target_dashboard_uid, target_payload, target_dashboard)
        if action == "move":
            container.remove(panel)
            _save_dashboard(client, ns.source_dashboard_uid, source_payload, source_dashboard)
        return {"panel": copied, "target": target_result, "moved": action == "move"}

    payload = client.dashboard_request("GET", ns.dashboard_uid)
    dashboard, _ = _dashboard_document(payload)
    if action == "list":
        return [panel for panel, _ in _walk_panels(dashboard.get("panels") or [])]
    panel, container = _find_panel(dashboard, ns.panel_id) if action != "create" else (None, None)
    if action != "create" and (panel is None or container is None):
        raise UsageError(f"panel {ns.panel_id!r} was not found")
    if action == "get":
        return panel
    if action == "query":
        return _query_panel(client, panel or {}, ns.time_from, ns.time_to)
    if action == "create":
        created = _body(ns)
        created.setdefault("id", _next_panel_id(dashboard))
        dashboard.setdefault("panels", []).append(created)
        result = _save_dashboard(client, ns.dashboard_uid, payload, dashboard)
        return {"panel": created, "dashboard": result}
    if action == "update":
        replacement = _body(ns)
        replacement.setdefault("id", panel.get("id"))
        container[container.index(panel)] = replacement
        result = _save_dashboard(client, ns.dashboard_uid, payload, dashboard)
        return {"panel": replacement, "dashboard": result}
    container.remove(panel)
    return _save_dashboard(client, ns.dashboard_uid, payload, dashboard)


def _save_dashboard(client: GrafanaClient, uid: str, payload: Any, dashboard: dict[str, Any]) -> Any:
    if isinstance(payload, dict) and "spec" in payload:
        document = copy.deepcopy(payload)
        document["spec"] = dashboard
        return client.dashboard_request("PUT", uid, json_body=document)
    metadata = payload.get("meta") if isinstance(payload, dict) else {}
    body = {"dashboard": dashboard, "overwrite": True}
    if isinstance(metadata, dict) and metadata.get("folderUid"):
        body["folderUid"] = metadata["folderUid"]
    return client.request("POST", "/api/dashboards/db", json_body=body)


def _query_panel(client: GrafanaClient, panel: dict[str, Any], time_from: str, time_to: str) -> Any:
    queries = copy.deepcopy(panel.get("targets") or [])
    panel_ds = panel.get("datasource")
    for query in queries:
        if isinstance(query, dict) and not query.get("datasource") and panel_ds:
            query["datasource"] = panel_ds
    body = {"queries": queries, "from": time_from, "to": time_to}
    return client.request("POST", "/api/ds/query", json_body=body)


def _run_panel_query(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> Any:
    payload = client.dashboard_request("GET", ns.dashboard_uid)
    dashboard, _ = _dashboard_document(payload)
    panel, _ = _find_panel(dashboard, ns.panel_id)
    if panel is None:
        raise UsageError(f"panel {ns.panel_id!r} was not found")
    return _query_panel(client, panel, ns.time_from, ns.time_to)


def _walk_panels(values: list[Any]) -> Iterator[tuple[dict[str, Any], list[Any]]]:
    for panel in values:
        if not isinstance(panel, dict):
            continue
        yield panel, values
        nested = panel.get("panels")
        if isinstance(nested, list):
            yield from _walk_panels(nested)


def _find_panel(dashboard: dict[str, Any], panel_id: Any) -> tuple[dict[str, Any] | None, list[Any] | None]:
    needle = str(panel_id)
    for panel, container in _walk_panels(dashboard.get("panels") or []):
        if str(panel.get("id")) == needle:
            return panel, container
    return None, None


def _next_panel_id(dashboard: dict[str, Any]) -> int:
    ids = [panel.get("id") for panel, _ in _walk_panels(dashboard.get("panels") or [])]
    numeric = [value for value in ids if isinstance(value, int)]
    return max(numeric, default=0) + 1


def _serving_target(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> dict[str, Any]:
    return {"platform": "grafana", "serving_datasource": settings.serving_datasource, "serving_database_name": settings.serving_database_name}


def _export_context(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> dict[str, Any]:
    return export_dashboard(
        client, ns.dashboard_uid, output_root=ns.output_root,
        include_hidden=ns.include_hidden, overwrite=ns.overwrite,
        instance_url=settings.base_url, profile_name=settings.profile_name,
        default_datasource_uid=settings.default_datasource_uid,
    )


def _api_call(client: GrafanaClient, settings: Settings, ns: argparse.Namespace) -> Any:
    data = client.request(ns.method, ns.path, params=_params(ns.param), json_body=_body(ns, optional=True), raw=True)
    if ns.output_file:
        destination = _safe_workspace_file(ns.output_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, indent=2, default=str).encode() + b"\n"
        destination.write_bytes(content)
        return {"output_file": str(destination), "bytes": len(content)}
    return data


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--output", choices=FORMATS, default="json")


def _add_body_options(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--json", dest="json_body")
    group.add_argument("--json-file")


def _body(ns: argparse.Namespace, *, optional: bool = False) -> dict[str, Any] | None:
    raw = getattr(ns, "json_body", None)
    if getattr(ns, "json_file", None):
        path = _safe_workspace_file(ns.json_file)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read JSON file {ns.json_file!r}: {exc}") from exc
    if raw is None:
        if optional:
            return None
        raise UsageError("a JSON request body is required")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise UsageError(f"invalid JSON body: {exc}") from exc
    if not isinstance(value, dict):
        raise UsageError("request body must be a JSON object")
    return value


def _params(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise UsageError(f"query parameter must be KEY=VALUE: {value!r}")
        key, item = value.split("=", 1)
        if not key:
            raise UsageError("query parameter key cannot be empty")
        result[key] = item
    return result


def _safe_workspace_file(raw: str) -> Path:
    cwd = Path.cwd().resolve()
    path = Path(raw)
    resolved = (path if path.is_absolute() else cwd / path).resolve()
    if resolved != cwd and cwd not in resolved.parents:
        raise UsageError("file must be inside the current project workspace")
    return resolved
