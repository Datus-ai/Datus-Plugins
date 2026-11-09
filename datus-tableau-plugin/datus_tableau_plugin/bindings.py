"""Explicit file-input to warehouse-table bindings; no filesystem inference."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .errors import UsageError
from .transforms import _table

MAX_BINDINGS_BYTES = 1024 * 1024


def read_bindings(path: str | Path) -> dict:
    source = Path(path)
    if (
        source.is_symlink()
        or not source.is_file()
        or source.stat().st_size > MAX_BINDINGS_BYTES
    ):
        raise UsageError("source bindings must be a bounded regular JSON file")
    try:
        document = json.loads(source.read_bytes())
    except (ValueError, OSError) as exc:
        raise UsageError("invalid source bindings JSON") from exc
    if not isinstance(document, dict):
        raise UsageError(
            "source bindings must be an object keyed by full transform identity"
        )
    return document


def apply_source_bindings(
    assets: list[dict], bindings: dict | None, *, dialect="duckdb"
) -> list[dict]:
    """Validate exact native input schemas and bind copied derived assets only."""
    if bindings is None:
        return assets
    if not isinstance(bindings, dict):
        raise UsageError("source bindings must be an object")
    output = deepcopy(assets)
    sources = {
        a["id"]: a
        for a in output
        if a.get("kind") == "transform"
        and a.get("definition", {}).get("operation", {}).get("type") == "source"
        and a["definition"]["operation"].get("binding_required")
    }
    for key, binding in bindings.items():
        if key not in sources:
            raise UsageError(f"source binding does not identify a parsed source: {key}")
        if (
            not isinstance(binding, dict)
            or set(binding) - {"table", "columns"}
            or "table" not in binding
        ):
            raise UsageError(
                "each source binding requires table and optional exact ordered columns"
            )
        try:
            _table(binding["table"], dialect)
        except (ValueError, TypeError) as exc:
            raise UsageError(f"invalid source table binding for {key}") from exc
        operation = sources[key]["definition"]["operation"]
        columns = binding.get("columns")
        if columns is not None and (
            not isinstance(columns, list)
            or not columns
            or not all(isinstance(c, str) and c for c in columns)
            or len(set(columns)) != len(columns)
        ):
            raise UsageError(
                "binding columns must be unique nonempty ordered field names"
            )
        native_columns = operation.get("columns")
        if (
            columns is not None
            and native_columns is not None
            and columns != native_columns
        ):
            raise UsageError(
                f"bound columns differ from the exact native source schema for {key}"
            )
        if columns is not None and native_columns is None:
            operation["columns"] = columns
        operation["table"] = binding["table"]
        operation["binding"] = {
            "origin": "explicit_source_binding",
            "table": binding["table"],
            "columns": native_columns if native_columns is not None else columns,
            "sha256": hashlib.sha256(
                json.dumps(binding, sort_keys=True).encode()
            ).hexdigest(),
            "physical_schema": "not_verified",
        }
    return output


def required_bindings(assets):
    return [
        {
            "id": a["id"],
            "name": a.get("name"),
            "columns": a["definition"]["operation"].get("columns"),
            "source_kind": a["definition"]["operation"].get("source_kind"),
        }
        for a in assets
        if a.get("kind") == "transform"
        and a.get("definition", {}).get("operation", {}).get("binding_required")
        and not a["definition"]["operation"].get("binding")
    ]
