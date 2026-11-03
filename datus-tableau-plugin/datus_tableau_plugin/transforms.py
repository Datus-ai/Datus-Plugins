"""Generate reviewable SELECT models from an explicit, bounded operation graph.

No connection is opened and no warehouse object is modified. Parsed native
operations lacking sufficient semantics are deliberately left unsupported.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

import sqlglot
from sqlglot import exp


def _name(asset_id: str) -> str:
    return "node_" + hashlib.sha256(asset_id.encode()).hexdigest()[:16]


def _quoted(name: str, dialect: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("column names must be non-empty strings")
    return exp.Identifier(this=name, quoted=True).sql(dialect=dialect)


def _table(name: str, dialect: str) -> str:
    # Only identifiers, not FROM fragments, joins or executable SQL.
    if not isinstance(name, str):
        raise ValueError("source table is missing")
    identifier = r'(?:\[[^\]]+\]|"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
    if not re.fullmatch(rf"\s*{identifier}(?:\s*\.\s*{identifier}){{0,3}}\s*", name):
        raise ValueError("source table must be a qualified identifier")
    parts = re.findall(r'\[([^\]]+)\]|"([^"]+)"|([A-Za-z_][A-Za-z0-9_$]*)', name)
    stripped = re.sub(r'\[[^\]]+\]|"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*', "", name)
    if not parts or re.sub(r"[.\s]", "", stripped) or len(parts) > 4:
        raise ValueError("source table must be a qualified identifier")
    return ".".join(
        _quoted(next(value for value in part if value), dialect) for part in parts
    )


def _select(sql: str, dialect: str, source_dialect: str | None = None) -> str:
    statements = sqlglot.parse(sql, read=source_dialect or dialect)
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("only one read-only SELECT query is supported")
    tree = statements[0]
    for node in tree.walk():
        if isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into)):
            raise ValueError("data-changing SQL is not a processing model")
    return tree.sql(dialect=dialect, pretty=True)


def _expression(value: Any, operation: dict, dialect: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("SQL expression is missing")
    # SQL-like text in a Tableau calculation is not proof of equivalent SQL.
    if operation.get("expression_language") != "sql":
        raise ValueError("expression requires explicit SQL-language mapping")
    parsed = sqlglot.parse(value, read=operation.get("source_dialect") or dialect)
    if len(parsed) != 1:
        raise ValueError("expected one scalar SQL expression")
    tree = parsed[0]
    if not isinstance(tree, exp.Expression) or any(
        isinstance(n, (exp.Query, exp.DDL, exp.DML, exp.Command)) for n in tree.walk()
    ):
        raise ValueError("expected a scalar SQL expression")
    return tree.sql(dialect=dialect)


def _compile(operation: dict, inputs: list[str], dialect: str) -> str:
    kind = operation.get("type")
    if kind == "source":
        if inputs:
            raise ValueError("source operations must not have graph inputs")
        if operation.get("sql"):
            return _select(operation["sql"], dialect, operation.get("source_dialect"))
        return f"SELECT * FROM {_table(operation.get('table'), dialect)}"
    if kind == "union":
        if len(inputs) < 2:
            raise ValueError("union requires at least two inputs")
        columns = operation.get("columns")
        if (
            not isinstance(columns, list)
            or not columns
            or operation.get("alignment") != "explicit"
        ):
            raise ValueError(
                "union requires explicit ordered common columns; never infer SELECT * alignment"
            )
        projection = ", ".join(_quoted(column, dialect) for column in columns)
        if operation.get("mode", "all") not in {"all", "distinct"}:
            raise ValueError("unknown union duplicate semantics")
        join = "\nUNION ALL\n" if operation.get("mode", "all") == "all" else "\nUNION\n"
        return join.join(f"SELECT {projection} FROM {source}" for source in inputs)
    if kind == "join":
        if len(inputs) != 2:
            raise ValueError("join requires exactly two ordered inputs")
        join_type = str(operation.get("join_type", "")).upper()
        if join_type not in {"INNER", "LEFT", "RIGHT", "FULL"}:
            raise ValueError("join type must be explicitly specified")
        projection = operation.get("projection")
        if not isinstance(projection, list) or not projection:
            raise ValueError(
                "join requires explicit field projection to avoid ambiguous duplicate columns"
            )
        columns = []
        for field in projection:
            if not isinstance(field, dict) or field.get("side") not in {
                "left",
                "right",
            }:
                raise ValueError("join field must identify its input side")
            alias = "l" if field["side"] == "left" else "r"
            columns.append(
                f"{alias}.{_quoted(field['column'], dialect)} AS {_quoted(field.get('name', field['column']), dialect)}"
            )
        condition = _expression(operation.get("condition"), operation, dialect)
        return f"SELECT {', '.join(columns)} FROM {inputs[0]} AS l {join_type} JOIN {inputs[1]} AS r ON {condition}"
    if len(inputs) != 1:
        raise ValueError(f"{kind!r} requires exactly one input")
    source = inputs[0]
    if kind in {"output", "clean"}:
        if kind == "clean" and operation.get("changes"):
            raise ValueError("clean changes must be expanded into explicit operations")
        if kind == "output" and operation.get("write_mode") not in {
            None,
            "replace",
            "append",
            "incremental",
        }:
            raise ValueError("unknown output mode")
        return f"SELECT * FROM {source}"
    if kind == "filter":
        return f"SELECT * FROM {source} WHERE {_expression(operation.get('condition'), operation, dialect)}"
    if kind in {"project", "rename"}:
        columns = operation.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError(
                "projection/rename requires the complete ordered output field list"
            )
        mapping = operation.get("mapping", {})
        return f"SELECT {', '.join(_quoted(c, dialect) + ' AS ' + _quoted(mapping.get(c, c), dialect) for c in columns)} FROM {source}"
    if kind == "derive":
        expression = _expression(operation.get("expression"), operation, dialect)
        columns = operation.get("columns")
        name = operation.get("column_name")
        if not isinstance(columns, list) or not columns or not name or name in columns:
            raise ValueError(
                "derive requires known input columns and a new, unique column_name"
            )
        return f"SELECT {', '.join(_quoted(c, dialect) for c in columns)}, {expression} AS {_quoted(name, dialect)} FROM {source}"
    if kind == "aggregate":
        groups = operation.get("group_by", [])
        aggregations = operation.get("aggregations")
        if (
            not isinstance(groups, list)
            or not isinstance(aggregations, list)
            or not aggregations
        ):
            raise ValueError("aggregate requires group_by and explicit aggregations")
        names = set(groups)
        projection = [_quoted(column, dialect) for column in groups]
        for item in aggregations:
            if not isinstance(item, dict) or item.get("name") in names:
                raise ValueError("aggregate output names must be unique")
            names.add(item.get("name"))
            projection.append(
                f"{_expression(item.get('expression'), operation, dialect)} AS {_quoted(item.get('name'), dialect)}"
            )
        return f"SELECT {', '.join(projection)} FROM {source}" + (
            f" GROUP BY {', '.join(_quoted(c, dialect) for c in groups)}"
            if groups
            else ""
        )
    if kind == "pivot":
        if operation.get("direction") != "columns_to_rows":
            raise ValueError(
                "row-to-column and dynamic pivots require explicit semantic mapping"
            )
        index, values = operation.get("index"), operation.get("values")
        if not isinstance(index, list) or not isinstance(values, list) or not values:
            raise ValueError(
                "pivot requires explicit retained columns and value columns"
            )
        names_to = _quoted(operation.get("names_to"), dialect)
        values_to = _quoted(operation.get("values_to"), dialect)
        projections = []
        for column in values:
            keep = [_quoted(c, dialect) for c in index]
            keep += [
                f"{exp.Literal.string(column).sql(dialect=dialect)} AS {names_to}",
                f"{_quoted(column, dialect)} AS {values_to}",
            ]
            condition = (
                ""
                if operation.get("include_nulls")
                else f" WHERE {_quoted(column, dialect)} IS NOT NULL"
            )
            projections.append(f"SELECT {', '.join(keep)} FROM {source}{condition}")
        return "\nUNION ALL\n".join(projections)
    raise ValueError(f"operation {kind!r} has no verified SQL translation")


def generate_transforms(
    assets: list[dict], dependencies: list[dict], *, dialect: str = "duckdb"
) -> dict:
    """Return files and a validation report; never execute or materialize models.

    Model SQL embeds each ancestor as a CTE, making every emitted SELECT
    independently inspectable. A failed/unknown node blocks all descendants.
    """
    # Validate the dialect even for an empty graph.
    sqlglot.Dialect.get_or_raise(dialect)
    transforms = {
        item["id"]: item
        for item in assets
        if item.get("kind") == "transform"
        and item.get("definition", {}).get("operation", {}).get("type") != "flow"
    }
    if len(transforms) != sum(
        item.get("kind") == "transform"
        and item.get("definition", {}).get("operation", {}).get("type") != "flow"
        for item in assets
    ):
        raise ValueError("duplicate transform identity")
    parents: dict[str, list[str]] = defaultdict(list)
    for asset_id, item in transforms.items():
        for input_id in item.get("definition", {}).get("inputs", []):
            if input_id not in parents[asset_id]:
                parents[asset_id].append(input_id)
    for edge in dependencies:
        if (
            edge.get("relation", "depends_on") == "depends_on"
            and edge.get("from") in transforms
        ):
            if edge["to"] not in parents[edge["from"]]:
                parents[edge["from"]].append(edge["to"])
    rows: dict[str, dict] = {}
    order: list[str] = []
    raw_sql: dict[str, str] = {}
    visiting: set[str] = set()
    ancestors: dict[str, set[str]] = {}

    def visit(asset_id: str) -> None:
        if asset_id in rows:
            return
        if asset_id in visiting:
            raise ValueError("processing graph contains a cycle")
        visiting.add(asset_id)
        item = transforms[asset_id]
        error = None
        lineage: set[str] = set()
        for parent in parents[asset_id]:
            if parent not in transforms:
                error = f"missing transform dependency {parent}"
                continue
            visit(parent)
            lineage.add(parent)
            lineage.update(ancestors[parent])
            if rows[parent]["status"] != "generated":
                error = f"dependency {parent} is not generated"
        operation = item.get("definition", {}).get("operation", {})
        if operation.get("type") == "join":
            explicit = item.get("definition", {}).get("inputs", [])
            if (
                len(explicit) != 2
                or len(set(explicit)) != 2
                or set(explicit) != set(parents[asset_id])
            ):
                error = "join requires exactly two explicitly ordered input identities matching its dependencies"
        if item.get("status", "ok") != "ok":
            error = "native operation is incomplete or requires an explicit verified semantic mapping"
        if error is None:
            try:
                statement = _compile(
                    operation, [_name(parent) for parent in parents[asset_id]], dialect
                )
                raw_sql[asset_id] = _select(statement, dialect)
            except (
                ValueError,
                sqlglot.errors.SqlglotError,
                KeyError,
                TypeError,
            ) as exc:
                error = str(exc)
        ancestors[asset_id] = lineage
        rows[asset_id] = {
            "id": asset_id,
            "inputs": parents[asset_id],
            "operation": operation.get("type"),
            "status": "blocked" if error else "generated",
            "error": error,
            "origin": "derived",
            "incremental": item.get("definition", {}).get("incremental"),
            "parameters": item.get("definition", {}).get("parameters", {}),
        }
        order.append(asset_id)
        visiting.remove(asset_id)

    cycle_error = None
    try:
        for asset_id in sorted(transforms):
            visit(asset_id)
    except ValueError as exc:
        cycle_error = str(exc)
        for asset_id in transforms:
            rows[asset_id] = {
                "id": asset_id,
                "inputs": parents[asset_id],
                "status": "blocked",
                "error": cycle_error,
                "origin": "derived",
            }
        raw_sql.clear()
        order.clear()
    models = {}
    for asset_id in order:
        if rows[asset_id]["status"] != "generated":
            continue
        ctes = [
            f"{_name(parent)} AS (\n{raw_sql[parent]}\n)"
            for parent in order
            if parent in ancestors[asset_id]
        ]
        sql = (("WITH " + ",\n".join(ctes) + "\n") if ctes else "") + raw_sql[asset_id]
        sql = _select(sql, dialect) + ";\n"
        path = f"models/{_name(asset_id)}.sql"
        models[path] = sql
        rows[asset_id]["file"] = path
    blocked = sum(row["status"] == "blocked" for row in rows.values())
    return {
        "models": models,
        "transforms": {
            f"transforms/{_name(asset_id)}.json": item
            for asset_id, item in transforms.items()
        },
        "pipeline": {
            "schema_version": 1,
            "dialect": dialect,
            "origin": "derived",
            "order": order,
            "nodes": [rows[asset_id] for asset_id in sorted(rows)],
            "status": "partial" if blocked else "generated",
            "execution": "not_run",
            "cycle": cycle_error,
        },
        "validation": {
            "schema_version": 1,
            "generated": len(models),
            "blocked": blocked,
            "checks": {
                "acyclic": cycle_error is None,
                "syntax": "sqlglot",
                "dialect": dialect,
                "physical_schema": "not_verified",
                "data_types": "not_verified",
                "data": "not_verified",
            },
            "execution": "not_run",
        },
    }
