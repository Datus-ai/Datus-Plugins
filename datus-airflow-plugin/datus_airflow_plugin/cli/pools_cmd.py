"""`datus airflow pools ...` — pool CRUD + import/export."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from ..errors import ApiError, UsageError
from ..output import render_one, render_rows
from . import Context, add_output_option, quote_path_part

POOL_COLUMNS = ["name", "slots", "open_slots", "occupied_slots", "include_deferred", "description"]


def register(sub: argparse._SubParsersAction) -> None:
    pools = sub.add_parser("pools", help="manage worker slot pools")
    group = pools.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list pools")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="show one pool")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("set", help="create or update a pool")
    p.add_argument("name")
    p.add_argument("slots", type=int)
    p.add_argument("description")
    p.add_argument(
        "--include-deferred", action="store_true", help="deferred tasks count towards the pool"
    )
    p.set_defaults(func=cmd_set)

    p = group.add_parser("delete", help="delete a pool")
    p.add_argument("name")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("import", help="import pools from a JSON file")
    p.add_argument("file", help='JSON file: {"pool_name": {"slots": 5, "description": "..."}}')
    p.set_defaults(func=cmd_import)

    p = group.add_parser("export", help="export all pools to a JSON file")
    p.add_argument("file", help="output path, or - for stdout")
    p.set_defaults(func=cmd_export)


def _upsert_pool(ctx: Context, name: str, slots: int, description: Any, include_deferred: bool) -> str:
    body = {
        "name": name,
        "slots": slots,
        "description": description,
        "include_deferred": include_deferred,
    }
    try:
        ctx.client.request("POST", "/pools", json_body=body)
        return "created"
    except ApiError as exc:
        if exc.status_code != 409:
            raise
    ctx.client.request(
        "PATCH",
        f"/pools/{quote_path_part(name)}",
        params={"update_mask": "slots,description,include_deferred"},
        json_body={"slots": slots, "description": description, "include_deferred": include_deferred},
    )
    return "updated"


# ---------------------------------------------------------------- handlers


def cmd_list(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/pools", "pools")
    print(render_rows(rows, POOL_COLUMNS, ns.output))
    return 0


def cmd_get(ctx: Context, ns) -> int:
    data = ctx.client.request("GET", f"/pools/{quote_path_part(ns.name)}")
    print(render_one(data, ns.output))
    return 0


def cmd_set(ctx: Context, ns) -> int:
    action = _upsert_pool(ctx, ns.name, ns.slots, ns.description, ns.include_deferred)
    print(f"{action} pool {ns.name} ({ns.slots} slots)")
    return 0


def cmd_delete(ctx: Context, ns) -> int:
    ctx.client.request("DELETE", f"/pools/{quote_path_part(ns.name)}")
    print(f"deleted pool {ns.name}")
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
        raise UsageError(f"{ns.file} must contain a JSON object of pool_name -> config")

    count = 0
    for name, conf in data.items():
        if not isinstance(conf, dict) or "slots" not in conf:
            raise UsageError(f"pool {name!r} must be an object with at least a `slots` field")
        _upsert_pool(
            ctx,
            name,
            int(conf["slots"]),
            conf.get("description"),
            bool(conf.get("include_deferred", False)),
        )
        count += 1
    print(f"imported {count} pool(s)")
    return 0


def cmd_export(ctx: Context, ns) -> int:
    rows = ctx.client.paginate("/pools", "pools")
    data: Dict[str, Any] = {
        row["name"]: {
            "slots": row.get("slots"),
            "description": row.get("description"),
            "include_deferred": row.get("include_deferred", False),
        }
        for row in rows
    }
    text = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=True)
    if ns.file == "-":
        print(text)
    else:
        with open(ns.file, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"exported {len(data)} pool(s) to {ns.file}")
    return 0
