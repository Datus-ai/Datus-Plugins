"""`datus airflow connections ...` — connection CRUD, test, import/export.

Passwords are masked in list/get output unless --show-secrets is given;
export keeps real values (that is its purpose) and warns on stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from ..conn_format import CONN_FIELDS, build_conn_uri, normalize_conn_fields, parse_conn_uri
from ..errors import ApiError, UsageError
from ..output import render_one, render_rows
from . import Context, add_output_option, parse_json_arg, quote_path_part

MASK = "***"


def register(sub: argparse._SubParsersAction) -> None:
    connections = sub.add_parser("connections", help="manage Airflow connections")
    group = connections.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list connections")
    p.add_argument("--limit", type=int, help="stop after N connections (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="show one connection")
    p.add_argument("conn_id")
    p.add_argument("--show-secrets", action="store_true", help="do not mask password/extra")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("add", help="create a connection")
    p.add_argument("conn_id")
    _add_conn_field_options(p)
    p.set_defaults(func=cmd_add)

    p = group.add_parser("delete", help="delete a connection")
    p.add_argument("conn_id")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser(
        "test",
        help="test a connection (server must enable [core] test_connection)",
        description=(
            "Test an existing connection by id, or an ad-hoc one described with "
            "--conn-uri/--conn-json/--conn-type options."
        ),
    )
    p.add_argument("conn_id", nargs="?", help="existing connection id to test")
    _add_conn_field_options(p)
    p.set_defaults(func=cmd_test)

    p = group.add_parser("import", help="import connections from a json/yaml/env file")
    p.add_argument("file")
    p.add_argument("--overwrite", action="store_true", help="update connections that already exist")
    p.set_defaults(func=cmd_import)

    p = group.add_parser("export", help="export all connections to a json/yaml/env file")
    p.add_argument("file", help="output path, or - for stdout (json)")
    p.add_argument(
        "--file-format",
        choices=("json", "yaml", "env"),
        help="default: by file extension (.json/.yaml/.yml/.env)",
    )
    p.set_defaults(func=cmd_export)


def _add_conn_field_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--conn-uri", help="connection URI (conn-type://login:password@host:port/schema)")
    p.add_argument("--conn-json", help="JSON object with connection fields")
    p.add_argument("--conn-type", help="connection type, e.g. postgres, aws, http")
    p.add_argument("--conn-host")
    p.add_argument("--conn-login")
    p.add_argument("--conn-password")
    p.add_argument("--conn-schema")
    p.add_argument("--conn-port", type=int)
    p.add_argument("--conn-extra", help="JSON string stored as extra")
    p.add_argument("--conn-description")


def _fields_from_args(ns) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    if ns.conn_uri:
        fields.update(parse_conn_uri(ns.conn_uri))
    if ns.conn_json:
        parsed = parse_json_arg(ns.conn_json, "--conn-json")
        if not isinstance(parsed, dict):
            raise UsageError("--conn-json must be a JSON object")
        fields.update(normalize_conn_fields(parsed, "--conn-json"))
    for option, key in (
        ("conn_type", "conn_type"),
        ("conn_host", "host"),
        ("conn_login", "login"),
        ("conn_password", "password"),
        ("conn_schema", "schema"),
        ("conn_port", "port"),
        ("conn_extra", "extra"),
        ("conn_description", "description"),
    ):
        value = getattr(ns, option)
        if value is not None:
            fields[key] = value
    return fields


def _mask(row: Dict[str, Any], show_secrets: bool) -> Dict[str, Any]:
    if show_secrets:
        return row
    masked = dict(row)
    if masked.get("password"):
        masked["password"] = MASK
    return masked


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    rows = [
        _mask(row, show_secrets=False)
        for row in ctx.client.paginate("/connections", "connections", limit=ns.limit)
    ]
    columns = ["connection_id", "conn_type", "description", "host", "port"]
    print(render_rows(rows, columns, ns.output))
    return 0


def cmd_get(ctx: Context, ns) -> int:
    data = ctx.client.request("GET", f"/connections/{quote_path_part(ns.conn_id)}")
    print(render_one(_mask(data, ns.show_secrets), ns.output))
    return 0


def cmd_add(ctx: Context, ns) -> int:
    fields = _fields_from_args(ns)
    if not fields.get("conn_type"):
        raise UsageError("a connection type is required (--conn-type, --conn-uri or --conn-json)")
    body = {"connection_id": ns.conn_id, **fields}
    ctx.client.request("POST", "/connections", json_body=body)
    print(f"created connection {ns.conn_id}")
    return 0


def cmd_delete(ctx: Context, ns) -> int:
    ctx.client.request("DELETE", f"/connections/{quote_path_part(ns.conn_id)}")
    print(f"deleted connection {ns.conn_id}")
    return 0


def cmd_test(ctx: Context, ns) -> int:
    fields = _fields_from_args(ns)
    conn_id = ns.conn_id or "datus_test_connection"
    if ns.conn_id and not fields:
        existing = ctx.client.request("GET", f"/connections/{quote_path_part(ns.conn_id)}")
        fields = {k: v for k, v in existing.items() if k in CONN_FIELDS and v is not None}
    if not fields.get("conn_type"):
        raise UsageError("nothing to test: pass a conn_id or --conn-uri/--conn-json/--conn-type")
    body = {"connection_id": conn_id, **fields}
    try:
        result = ctx.client.request("POST", "/connections/test", json_body=body)
    except ApiError as exc:
        if exc.status_code == 403:
            raise ApiError(
                f"{exc} — connection testing is disabled by default; set "
                "AIRFLOW__CORE__TEST_CONNECTION=Enabled on the server",
                status_code=403,
            ) from exc
        raise
    status = bool(result.get("status"))
    print(f"{'success' if status else 'failed'}: {result.get('message', '')}")
    return 0 if status else 1


def cmd_import(ctx: Context, ns) -> int:
    conns = _load_connections_file(ns.file)
    created = updated = skipped = 0
    for conn_id, fields in conns.items():
        body = {"connection_id": conn_id, **fields}
        try:
            ctx.client.request("POST", "/connections", json_body=body)
            created += 1
        except ApiError as exc:
            if exc.status_code != 409:
                raise
            if not ns.overwrite:
                print(f"skipped {conn_id} (already exists; use --overwrite)", file=sys.stderr)
                skipped += 1
                continue
            ctx.client.request(
                "PATCH", f"/connections/{quote_path_part(conn_id)}", json_body=body
            )
            updated += 1
    print(f"imported connections: {created} created, {updated} updated, {skipped} skipped")
    return 0


def _load_connections_file(path: str) -> Dict[str, Dict[str, Any]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc

    suffix = Path(path).suffix.lower()
    if suffix == ".env":
        result: Dict[str, Dict[str, Any]] = {}
        for line_no, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            conn_id, sep, uri = line.partition("=")
            if not sep:
                raise UsageError(f"{path}:{line_no}: expected CONN_ID=uri")
            result[conn_id.strip()] = parse_conn_uri(uri.strip().strip("'\""))
        return result

    if suffix in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise UsageError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path} must contain a mapping of conn_id -> fields or URI")

    result = {}
    for conn_id, value in data.items():
        if isinstance(value, str):
            result[conn_id] = parse_conn_uri(value)
        elif isinstance(value, dict):
            result[conn_id] = normalize_conn_fields(value, f"{path}:{conn_id}")
        else:
            raise UsageError(f"{path}: connection {conn_id!r} must be a mapping or URI string")
    return result


def cmd_export(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/connections", "connections")
    fmt = ns.file_format
    if not fmt:
        suffix = Path(ns.file).suffix.lower()
        fmt = {"": "json", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".env": "env"}.get(suffix)
        if fmt is None:
            raise UsageError(f"cannot infer format from {ns.file!r}; pass --file-format")

    if fmt == "env":
        lines = []
        for row in rows:
            fields = {k: v for k, v in row.items() if k in CONN_FIELDS and v is not None}
            lines.append(f"{row['connection_id']}={build_conn_uri(fields)}")
        text = "\n".join(lines) + "\n"
    else:
        data = {
            row["connection_id"]: {k: row.get(k) for k in CONN_FIELDS}
            for row in rows
        }
        if fmt == "yaml":
            import yaml

            text = yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
        else:
            text = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=True) + "\n"

    print("warning: the export contains connection secrets in clear text", file=sys.stderr)
    if ns.file == "-":
        sys.stdout.write(text)
    else:
        with open(ns.file, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"exported {len(rows)} connection(s) to {ns.file}", file=sys.stderr)
    return 0
