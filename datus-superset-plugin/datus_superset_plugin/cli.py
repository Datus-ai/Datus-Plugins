"""CLI entry point for ``datus superset``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .client import SupersetClient
from .config import Settings
from .errors import EXIT_USAGE, PluginError, UsageError
from .exporter import export_dashboard
from .operations import OPERATIONS, Operation
from .output import FORMATS, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datus superset", description="Apache Superset data-plane CLI")
    groups = parser.add_subparsers(dest="group", required=True, metavar="<command>")
    for group_name, operations in OPERATIONS.items():
        group = groups.add_parser(group_name, help=f"Superset {group_name} operations")
        subs = group.add_subparsers(dest="subcommand", required=True)
        for name, operation in operations.items():
            command = subs.add_parser(name)
            for argument in operation.args:
                command.add_argument(argument)
            command.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="query parameter (repeatable)")
            if operation.body:
                _add_body_options(command)
            if operation.upload:
                command.add_argument("--file", required=True, help="project-local Superset export ZIP")
                command.add_argument("--password-env", help="environment variable containing the optional import password")
                command.add_argument("--overwrite", action="store_true")
            if operation.binary:
                command.add_argument("--output-file", required=True)
            else:
                _add_output(command)
            command.set_defaults(func=_run_operation, operation=operation)

    serving = groups.add_parser("serving-target", help="show the Datus datasource bound to this BI instance")
    _add_output(serving)
    serving.set_defaults(func=_serving_target)

    context = groups.add_parser("context", help="export dashboard queries into project context files")
    context_sub = context.add_subparsers(dest="subcommand", required=True)
    export = context_sub.add_parser("export-dashboard")
    export.add_argument("dashboard_id")
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
    client: SupersetClient | None = None
    try:
        settings = Settings.from_profile(profile)
        client = SupersetClient(settings)
        result = ns.func(client, settings, ns)
        if result is not None:
            print(render(result, getattr(ns, "output", "json")))
        return 0
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        print(f"error: cannot reach Superset: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: Superset request failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        if client:
            client.close()


def _run_operation(client: SupersetClient, settings: Settings, ns: argparse.Namespace) -> Any:
    operation: Operation = ns.operation
    path = operation.path
    for argument in operation.args:
        path = path.replace("{" + argument + "}", quote(str(getattr(ns, argument)), safe=""))
    params = _params(ns.param)
    body = _body(ns) if operation.body else None
    if operation.upload:
        source = _safe_input_file(ns.file)
        try:
            handle = source.open("rb")
        except OSError as exc:
            raise UsageError(f"cannot read import file {ns.file!r}: {exc}") from exc
        try:
            form = {"overwrite": "true" if ns.overwrite else "false"}
            if ns.password_env:
                password = os.environ.get(ns.password_env)
                if password is None:
                    raise UsageError(f"import password environment variable is not set: {ns.password_env}")
                form["passwords"] = json.dumps({"*": password})
            data = client.request(
                operation.method, path, params=params, data=form,
                files={"formData": (source.name, handle, "application/zip")},
            )
        finally:
            handle.close()
    else:
        data = client.request(operation.method, path, params=params, json_body=body)
    if operation.binary:
        destination = _safe_output_file(ns.output_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = data if isinstance(data, bytes) else str(data or "").encode()
        destination.write_bytes(payload)
        return {"output_file": str(destination), "bytes": len(payload)}
    return data


def _serving_target(client: SupersetClient, settings: Settings, ns: argparse.Namespace) -> dict[str, Any]:
    return {
        "platform": "superset",
        "serving_datasource": settings.serving_datasource,
        "serving_database_name": settings.serving_database_name,
    }


def _export_context(client: SupersetClient, settings: Settings, ns: argparse.Namespace) -> dict[str, Any]:
    return export_dashboard(
        client,
        ns.dashboard_id,
        output_root=ns.output_root,
        include_hidden=ns.include_hidden,
        overwrite=ns.overwrite,
        instance_url=settings.base_url,
        profile_name=settings.profile_name,
    )


def _api_call(client: SupersetClient, settings: Settings, ns: argparse.Namespace) -> Any:
    body = _body(ns, optional=True)
    data = client.request(ns.method, ns.path, params=_params(ns.param), json_body=body, raw=True)
    if ns.output_file:
        destination = _safe_output_file(ns.output_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, indent=2).encode()
        destination.write_bytes(payload)
        return {"output_file": str(destination), "bytes": len(payload)}
    return data


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--output", choices=FORMATS, default="json")


def _add_body_options(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--json", dest="json_body")
    group.add_argument("--json-file")


def _body(ns: argparse.Namespace, *, optional: bool = False) -> dict[str, Any] | None:
    raw = getattr(ns, "json_body", None)
    path = getattr(ns, "json_file", None)
    if path:
        safe = _safe_input_file(path)
        try:
            raw = safe.read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read JSON file {path!r}: {exc}") from exc
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
    params: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise UsageError(f"query parameter must be KEY=VALUE: {value!r}")
        key, item = value.split("=", 1)
        if not key:
            raise UsageError("query parameter key cannot be empty")
        params[key] = item
    return params


def _safe_input_file(raw: str) -> Path:
    path = Path(raw).resolve()
    cwd = Path.cwd().resolve()
    if path != cwd and cwd not in path.parents:
        raise UsageError("input file must be inside the current project workspace")
    return path


def _safe_output_file(raw: str) -> Path:
    return _safe_input_file(raw)
