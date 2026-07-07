"""`datus airflow variables ...` — Airflow Variables CRUD + import/export."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from ..errors import ApiError, PluginError, UsageError
from ..output import render_rows
from . import Context, add_output_option, parse_json_arg, quote_path_part


def register(sub: argparse._SubParsersAction) -> None:
    variables = sub.add_parser("variables", help="manage Airflow Variables")
    group = variables.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list variables")
    p.add_argument("-p", "--pattern", help="filter keys with a SQL LIKE pattern (%% wildcard)")
    p.add_argument("--limit", type=int, help="stop after N variables (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="print the value of a variable")
    p.add_argument("key")
    p.add_argument("-d", "--default", help="value printed when the variable does not exist")
    p.set_defaults(func=cmd_get)

    p = group.add_parser("set", help="create or update a variable")
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--description", help="description stored with the variable")
    p.add_argument(
        "-j", "--json", action="store_true", help="parse VALUE as JSON instead of a raw string"
    )
    p.set_defaults(func=cmd_set)

    p = group.add_parser("delete", help="delete a variable")
    p.add_argument("key")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("import", help="import variables from a JSON file")
    p.add_argument("file", help="JSON file: {\"KEY\": value, ...}")
    p.add_argument(
        "--action-on-existing-key",
        choices=("overwrite", "fail", "skip"),
        default="overwrite",
        help="what to do when a key already exists (default: overwrite)",
    )
    p.set_defaults(func=cmd_import)

    p = group.add_parser("export", help="export all variables to a JSON file")
    p.add_argument("file", help="output path, or - for stdout")
    p.set_defaults(func=cmd_export)


def _set_variable(ctx: Context, key: str, value: Any, description: str | None) -> str:
    body: Dict[str, Any] = {"key": key, "value": value, "description": description}
    try:
        ctx.client.request("POST", "/variables", json_body=body)
        return "created"
    except ApiError as exc:
        if exc.status_code != 409:
            raise
    ctx.client.request("PATCH", f"/variables/{quote_path_part(key)}", json_body=body)
    return "updated"


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    rows = ctx.client.paginate(
        "/variables",
        "variables",
        params={"variable_key_pattern": ns.pattern},
        limit=ns.limit,
    )
    print(render_rows(rows, ["key", "value", "description", "is_encrypted"], ns.output))
    return 0


def cmd_get(ctx: Context, ns) -> int:
    try:
        data = ctx.client.request("GET", f"/variables/{quote_path_part(ns.key)}")
    except ApiError as exc:
        if exc.status_code == 404 and ns.default is not None:
            print(ns.default)
            return 0
        raise
    print(data.get("value"))
    return 0


def cmd_set(ctx: Context, ns) -> int:
    value: Any = parse_json_arg(ns.value, "VALUE") if ns.json else ns.value
    action = _set_variable(ctx, ns.key, value, ns.description)
    print(f"{action} variable {ns.key}")
    return 0


def cmd_delete(ctx: Context, ns) -> int:
    ctx.client.request("DELETE", f"/variables/{quote_path_part(ns.key)}")
    print(f"deleted variable {ns.key}")
    return 0


def cmd_import(ctx: Context, ns) -> int:
    try:
        with open(ns.file, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise UsageError(f"cannot read {ns.file}: {exc}") from exc
    except ValueError as exc:
        raise UsageError(f"{ns.file} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{ns.file} must contain a JSON object of key -> value")

    existing = {row["key"] for row in ctx.client.paginate("/variables", "variables")}
    created = updated = skipped = 0
    failed: list[str] = []
    for key, value in data.items():
        if key in existing:
            if ns.action_on_existing_key == "skip":
                skipped += 1
                continue
            if ns.action_on_existing_key == "fail":
                failed.append(key)
                continue
        action = _set_variable(ctx, key, value, None)
        if action == "created":
            created += 1
        else:
            updated += 1
    print(f"imported variables: {created} created, {updated} updated, {skipped} skipped")
    if failed:
        raise PluginError(f"key(s) already exist (--action-on-existing-key=fail): {', '.join(failed)}")
    return 0


def cmd_export(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/variables", "variables")
    data = {row["key"]: row.get("value") for row in rows}
    text = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=True)
    if ns.file == "-":
        print(text)
    else:
        with open(ns.file, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"exported {len(data)} variable(s) to {ns.file}", file=sys.stderr)
    return 0
