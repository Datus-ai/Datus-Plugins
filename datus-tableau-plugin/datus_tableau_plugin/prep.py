"""Normalize bounded native Prep ports, action chains and ordered field schemas."""

from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from .formula import literal, quote, translate

MAX_NATIVE_NODES = 4096
MAX_NATIVE_EDGES = 16384
MAX_SCHEMA_FIELDS = 2048
MAX_TOTAL_SCHEMA_FIELDS = 262144


def native_flow(flow):
    nodes = flow.get("nodes", {})
    if not isinstance(nodes, (dict, list)):
        raise ValueError("Prep flow nodes must be an object or array")
    return any(
        isinstance(n, dict)
        and n.get("baseType") in {"input", "transform", "container", "superNode"}
        and str(n.get("nodeType", "")).startswith(".")
        for n in (nodes.values() if isinstance(nodes, dict) else nodes)
    )


def normalize(flow, flow_id):
    """Expand wrappers to scoped operations; aliases route explicit native ports."""
    prefix = f"transform:{flow_id}:"
    records, aliases, links, controls = {}, {}, [], []
    declared_ids = set()
    pending = deque([(flow.get("nodes", {}), "")])

    def add(key, node):
        if key in records:
            raise ValueError("duplicate native Prep node identity")
        if len(records) + len(controls) >= MAX_NATIVE_NODES:
            raise ValueError("native Prep node resource limit")
        records[key] = {"node": node, "ports": {}}

    def loader_actions(key, node):
        """Apply each loader's native actions and final ordered selection."""
        actions = list(node.get("actions") or [])
        if node.get("selectedFields"):
            actions.append(
                {
                    "nodeType": ".v1.SelectFields",
                    "columnNames": node["selectedFields"],
                    "id": "selected-fields",
                }
            )
        tail = (key, "Raw", "out")
        for i, action in enumerate(actions):
            akey = key + f"/source-action/{i}:" + str(action.get("id", i))
            add(akey, action)
            links.append((tail, (akey, "Default", "in")))
            tail = (akey, "Default", "out")
        if actions:
            aliases[key, "Default", "out"] = tail
        return tail

    def chain(owner, annotations, inputs, phase):
        tails = dict(inputs)
        for index, annotation in enumerate(annotations):
            ns = annotation.get("namespace", "Default")
            node = annotation.get("annotationNode")
            if not isinstance(node, dict):
                raise ValueError("invalid native Prep annotation")
            key = f"{owner}/{phase}/{index}:{node.get('id', index)}"
            add(key, node)
            links.append((tails.get(ns, (owner, ns, "in")), (key, "Default", "in")))
            tails[ns] = (key, "Default", "out")
        return tails

    while pending:
        values, scope = pending.popleft()
        if not isinstance(values, (list, dict)):
            raise ValueError("invalid native Prep node collection")
        for identifier, node in (
            values.items() if isinstance(values, dict) else enumerate(values)
        ):
            if not isinstance(node, dict):
                raise ValueError("invalid native Prep node")
            key = scope + str(node.get("id") or identifier)
            if key in declared_ids:
                raise ValueError("duplicate native Prep node identity")
            declared_ids.add(key)
            if len(declared_ids) > MAX_NATIVE_NODES:
                raise ValueError("native Prep node resource limit")
            short = str(node.get("nodeType", "")).split(".")[-1]
            if node.get("nodeType") == ".v1.Container":
                controls.append(
                    (
                        key,
                        {
                            k: v
                            for k, v in node.items()
                            if k
                            not in {
                                "loomContainer",
                                "actionNode",
                                "beforeActionAnnotations",
                                "afterActionAnnotations",
                            }
                        },
                    )
                )
                nested = node.get("loomContainer", {}).get("nodes", {})
                pending.append((nested, key + "/"))
                for direction, field in [
                    ("in", "namespacesToInput"),
                    ("out", "namespacesToOutput"),
                ]:
                    for ns, endpoint in node.get(field, {}).items():
                        aliases[key, ns, direction] = (
                            key + "/" + str(endpoint["nodeId"]),
                            endpoint.get("namespace", "Default"),
                            direction,
                        )
                if (
                    not nested
                    and not node.get("namespacesToInput")
                    and not node.get("namespacesToOutput")
                ):
                    # An empty control has no executable operation or output.
                    aliases[key, "Default", "in"] = None
                    aliases[key, "Default", "out"] = None
            elif node.get("nodeType") in {
                ".v2018_2_3.SuperJoin",
                ".v2018_2_3.SuperUnion",
                ".v2018_2_3.SuperAggregate",
                ".v2018_2_3.SuperUnpivot",
            }:
                controls.append(
                    (
                        key,
                        {
                            k: v
                            for k, v in node.items()
                            if k
                            not in {
                                "loomContainer",
                                "actionNode",
                                "beforeActionAnnotations",
                                "afterActionAnnotations",
                            }
                        },
                    )
                )
                action = node.get("actionNode")
                if not isinstance(action, dict):
                    add(key, node)
                else:
                    action_key = key + "/action/" + str(action.get("id", "action"))
                    add(action_key, action)
                    namespaces = (
                        {"Left", "Right"}
                        if str(action.get("nodeType", "")).endswith(".SimpleJoin")
                        else {
                            item["namespaceName"]
                            for item in action.get("namespaceFieldMappings", [])
                        }
                        or {"Default"}
                    )
                    # Annotation input aliases point to first annotation; chains
                    # are wired per port in native array order.
                    tails = {}
                    for ns in sorted(namespaces):
                        selected = [
                            a
                            for a in node.get("beforeActionAnnotations", [])
                            if a.get("namespace", "Default") == ns
                        ]
                        if selected:
                            first = f"{key}/before/{node.get('beforeActionAnnotations', []).index(selected[0])}:{selected[0]['annotationNode'].get('id', 0)}"
                            aliases[key, ns, "in"] = (first, "Default", "in")
                        else:
                            aliases[key, ns, "in"] = (action_key, ns, "in")
                    for index, annotation in enumerate(
                        node.get("beforeActionAnnotations", [])
                    ):
                        ns = annotation.get("namespace", "Default")
                        a = annotation["annotationNode"]
                        akey = f"{key}/before/{index}:{a.get('id', index)}"
                        add(akey, a)
                        if ns in tails:
                            links.append((tails[ns], (akey, "Default", "in")))
                        tails[ns] = (akey, "Default", "out")
                    for ns, tail in tails.items():
                        links.append((tail, (action_key, ns, "in")))
                    after = chain(
                        key,
                        node.get("afterActionAnnotations", []),
                        {"Default": (action_key, "Default", "out")},
                        "after",
                    )
                    aliases[key, "Default", "out"] = after["Default"]
            elif node.get("nodeType") == ".v1.LoadCsvInputUnion":
                add(key, {**node, "nodeType": ".v1.SimpleUnion", "input_union": True})
                for i, entry in enumerate(node.get("generatedInputs", [])):
                    child = entry.get("inputNode", {})
                    ckey = key + "/generated/" + str(child.get("id", i))
                    field = node.get("pathNameField")
                    has_actions = bool(
                        child.get("actions") or child.get("selectedFields")
                    )
                    add(
                        ckey,
                        {
                            **child,
                            "_constants": {field: entry.get("filePath")}
                            if field and not has_actions
                            else {},
                        },
                    )
                    tail = loader_actions(ckey, child)
                    if field and has_actions:
                        # File Paths belongs to the union input, after the child
                        # loader has removed/projected its own physical fields.
                        provenance_key = ckey + "/file-provenance"
                        add(
                            provenance_key,
                            {
                                "nodeType": ".v1.FileProvenance",
                                "columnName": field,
                                "value": entry.get("filePath"),
                            },
                        )
                        links.append((tail, (provenance_key, "Default", "in")))
                        tail = (provenance_key, "Default", "out")
                    links.append((tail, (key, f"input:{i:06}", "in")))
            else:
                add(key, node)
            # Standalone and generated inputs share exactly the same loader chain.
            if short in {"LoadCsv", "LoadExcel", "LoadSql"}:
                loader_actions(key, node)
            for edge in node.get("nextNodes", []):
                if not isinstance(edge, dict) or not edge.get("nextNodeId"):
                    raise ValueError(
                        "native Prep edges require explicit ports and targets"
                    )
                links.append(
                    (
                        (key, edge.get("namespace", "Default"), "out"),
                        (
                            scope + str(edge["nextNodeId"]),
                            edge.get("nextNamespace", "Default"),
                            "in",
                        ),
                    )
                )
                if len(links) > MAX_NATIVE_EDGES:
                    raise ValueError("native Prep edge resource limit")

    def resolve(endpoint):
        seen = set()
        while endpoint in aliases:
            if endpoint in seen:
                raise ValueError("native Prep port alias cycle")
            seen.add(endpoint)
            endpoint = aliases[endpoint]
            if endpoint is None:
                return None
        if endpoint[0] not in records:
            raise ValueError("native Prep port references a missing node")
        if endpoint[2] == "out" and endpoint[1] not in {"Default", "Raw"}:
            records[endpoint[0]]["port_error"] = "unsupported native output namespace"
        return endpoint

    for producer, consumer in links:
        source, target = resolve(producer), resolve(consumer)
        if source is None or target is None:
            continue
        existing = records[target[0]]["ports"].get(target[1])
        if existing and existing != source[0]:
            records[target[0]]["port_error"] = (
                "multiple native inputs target the same namespace"
            )
        records[target[0]]["ports"][target[1]] = source[0]

    out = {
        "assets": [],
        "dependencies": [],
        "queries": [],
        "coverage": [],
        "documents": {},
    }
    root = {
        "id": f"transform:{flow_id}",
        "kind": "transform",
        "name": str(flow_id),
        "source": {"flow_luid": flow_id},
        "definition": {
            "operation": {"type": "flow"},
            "native_controls": [{"native_id": k, "node": n} for k, n in controls],
        },
        "status": "ok",
    }
    out["assets"].append(root)
    consumers = defaultdict(list)
    remaining = {}
    for key, record in records.items():
        remaining[key] = len(set(record["ports"].values()))
        for parent in set(record["ports"].values()):
            consumers[parent].append(key)
    queue = deque(k for k, count in remaining.items() if not count)
    schemas = {}
    visited = set()
    schema_fields = 0
    while queue:
        key = queue.popleft()
        visited.add(key)
        record = records[key]
        ports = record["ports"]
        short = str(record["node"].get("nodeType", "")).split(".")[-1]
        order = ["Left", "Right"] if short == "SimpleJoin" else sorted(ports)
        parents = [ports[p] for p in order if p in ports]
        error = record.get("port_error")
        try:
            if error:
                raise ValueError(error)
            op, schema = operation(
                record["node"], {p: schemas.get(parent) for p, parent in ports.items()}
            )
            error = op.get("semantic_error")
        except (ValueError, KeyError, TypeError) as exc:
            op, schema, error = (
                {"type": "unsupported"},
                declared_schema(
                    record["node"],
                    {p: schemas.get(parent) for p, parent in ports.items()},
                ),
                str(exc),
            )
        if schema is not None:
            schema_fields += len(schema)
            if (
                len(schema) > MAX_SCHEMA_FIELDS
                or schema_fields > MAX_TOTAL_SCHEMA_FIELDS
            ):
                raise ValueError("native Prep field schema resource limit")
        schemas[key] = schema
        op.update(native_type=record["node"].get("nodeType"), native=record["node"])
        definition = {
            "operation": op,
            "inputs": [prefix + p for p in parents],
            "input_order": "explicit",
            "output_schema": schema,
            "normalization": {
                "origin": "native_prep",
                "version": 1,
                "port_inputs": {p: prefix + v for p, v in ports.items()},
            },
        }
        asset = {
            "id": prefix + key,
            "kind": "transform",
            "name": str(record["node"].get("name") or key),
            "source": {"flow_luid": flow_id, "native_id": key},
            "definition": definition,
            "status": "unsupported" if error else "ok",
        }
        if error:
            definition["normalization"]["error"] = error
            out["coverage"].append(
                {
                    "asset_id": asset["id"],
                    "status": "unsupported",
                    "code": "native_semantics_unresolved",
                    "message": error,
                }
            )
        out["assets"].append(asset)
        custom_sql = record["node"].get("customSql")
        if (
            record["node"].get("nodeType") == ".v1.LoadSql"
            and isinstance(custom_sql, str)
            and custom_sql.strip()
        ):
            identity = hashlib.sha256(
                (asset["id"] + "\x00" + custom_sql).encode()
            ).hexdigest()[:20]
            out["queries"].append(
                {
                    "id": "query:" + identity,
                    "asset_id": asset["id"],
                    "origin": "source",
                    "language": "sql",
                    "text": custom_sql,
                    "status": "ok",
                }
            )
        out["dependencies"].extend(
            {"from": asset["id"], "to": prefix + p, "relation": "depends_on"}
            for p in parents
        )
        out["dependencies"].append(
            {"from": root["id"], "to": asset["id"], "relation": "contains"}
        )
        for child in consumers[key]:
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    # Cycles remain explicit, allowing the compiler to identify affected nodes.
    for key in records.keys() - visited:
        record = records[key]
        out["assets"].append(
            {
                "id": prefix + key,
                "kind": "transform",
                "name": key,
                "source": {"flow_luid": flow_id, "native_id": key},
                "definition": {
                    "operation": {"type": "unsupported", "native": record["node"]},
                    "inputs": [prefix + p for p in record["ports"].values()],
                },
                "status": "unsupported",
            }
        )
        out["dependencies"].extend(
            {"from": prefix + key, "to": prefix + p, "relation": "depends_on"}
            for p in record["ports"].values()
        )
    return out


def fields(node):
    result = {}
    for field in node.get("fields", []):
        if (
            not isinstance(field, dict)
            or not isinstance(field.get("name"), str)
            or field["name"] in result
        ):
            raise ValueError("native input requires unique ordered named fields")
        result[field["name"]] = field.get("type", "unknown")
    if len(result) > MAX_SCHEMA_FIELDS:
        raise ValueError("native Prep source field resource limit")
    if not result:
        raise ValueError("native input has no explicit field schema")
    return result


def operation(node, ports):
    if not str(node.get("nodeType", "")).startswith(".v1."):
        raise ValueError("unsupported native Prep operation version")
    short = str(node.get("nodeType", "")).split(".")[-1]
    if short in {"LoadCsv", "LoadExcel", "LoadSql"}:
        custom_sql_unmapped = (
            short == "LoadSql"
            and isinstance(node.get("customSql"), str)
            and bool(node["customSql"].strip())
        )
        if any(
            node.get(k)
            for k in [
                "filters",
                "groupByFields",
                "restrictedFields",
                "userRenamedFields",
            ]
        ):
            raise ValueError(
                "native source contains unmapped read-time transformations"
            )
        schema = fields(node)
        constants = node.get("_constants", {})
        if any(
            not isinstance(k, str) or not k or not isinstance(v, str)
            for k, v in constants.items()
        ):
            raise ValueError("native file-union path provenance is missing")
        if set(constants) & set(schema):
            raise ValueError(
                "native file-union path provenance collides with physical fields"
            )
        schema.update({k: "string" for k in constants})
        return {
            "type": "source",
            "columns": list(fields(node)),
            "constants": constants,
            "binding_required": True,
            "source_kind": short,
            **(
                {
                    "semantic_error": "native LoadSql custom SQL requires an explicit verified query mapping"
                }
                if custom_sql_unmapped
                else {}
            ),
        }, schema
    if not ports or any(schema is None for schema in ports.values()):
        raise ValueError("input field schema is unavailable")
    if short == "SimpleJoin":
        if set(ports) != {"Left", "Right"}:
            raise ValueError(
                "native join requires explicit Left and Right namespace ports"
            )
        schema, projection = {}, []
        for side, source in [("left", ports["Left"]), ("right", ports["Right"])]:
            for name, dtype in source.items():
                alias, suffix = name, 1
                while alias in schema:
                    alias = f"{name}-{suffix}"
                    suffix += 1
                schema[alias] = dtype
                projection.append({"side": side, "column": name, "name": alias})
        conditions = []
        for condition in node.get("conditions", []):
            columns = []
            for label, side in [("left", "Left"), ("right", "Right")]:
                text = condition.get(label + "Expression", "")
                if (
                    not text.startswith("[")
                    or not text.endswith("]")
                    or text[1:-1] not in ports[side]
                ):
                    raise ValueError(
                        "native join supports explicit existing field references only"
                    )
                columns.append(
                    ("l" if side == "Left" else "r") + "." + quote(text[1:-1])
                )
            comparator = condition.get("comparator")
            if comparator not in {"==", "!=", "<", ">", "<=", ">="}:
                raise ValueError("unsupported native join comparator")
            conditions.append(
                f"{columns[0]} {'=' if comparator == '==' else comparator} {columns[1]}"
            )
        if not conditions:
            raise ValueError("native join condition is missing")
        return {
            "type": "join",
            "join_type": node.get("joinType"),
            "projection": projection,
            "condition": " AND ".join(conditions),
            "expression_language": "sql",
            "translation": "native_field_join_v1",
        }, schema
    if short == "SimpleUnion":
        mappings = node.get("namespaceFieldMappings", [])
        if any(m.get("fieldMappings") for m in mappings):
            raise ValueError("union field mappings require explicit semantic mapping")
        if mappings and {m["namespaceName"] for m in mappings} != set(ports):
            raise ValueError("union namespace mappings differ from input ports")
        columns = {}
        type_errors = []
        for schema in ports.values():
            for name, dtype in schema.items():
                if name in columns and columns[name] != dtype:
                    if {columns[name], dtype} <= {"date", "datetime"}:
                        columns[name] = "datetime"
                    elif {columns[name], dtype} <= {"integer", "real"}:
                        columns[name] = "real"
                    else:
                        columns[name] = "unknown"
                        type_errors.append(name)
                else:
                    columns[name] = dtype
        if not node.get("input_union"):
            columns["Table Names"] = "string"
        return {
            "type": "union",
            "columns": list(columns),
            "alignment": "explicit",
            "input_columns": [list(ports[p]) for p in sorted(ports)],
            "mode": "all",
            "native_type_conflicts": sorted(set(type_errors)),
            "native_provenance_unresolved": not node.get("input_union"),
            "semantic_issues": (
                [
                    {
                        "code": "native_union_type_conflict",
                        "fields": sorted(set(type_errors)),
                    }
                ]
                if type_errors
                else []
            )
            + (
                [{"code": "native_union_provenance_unresolved", "field": "Table Names"}]
                if not node.get("input_union")
                else []
            ),
        }, columns
    if set(ports) != {"Default"}:
        raise ValueError("native operation requires exactly one Default input port")
    schema = dict(next(iter(ports.values())))
    columns = list(schema)
    base = {
        "columns": columns,
        "expression_language": "sql",
        "source_dialect": "duckdb",
        "translation": "native_prep_v1",
    }
    if short == "FileProvenance":
        name, value = node.get("columnName"), node.get("value")
        if (
            not isinstance(name, str)
            or not name
            or name in schema
            or not isinstance(value, str)
        ):
            raise ValueError("native file-union path provenance is missing or collides")
        schema[name] = "string"
        return {
            **base,
            "type": "derive",
            "column_name": name,
            "expression": literal(value),
        }, schema
    if short == "SelectFields":
        selected = node.get("columnNames")
        if (
            not isinstance(selected, list)
            or not selected
            or len(set(selected)) != len(selected)
            or any(c not in schema for c in selected)
        ):
            raise ValueError("native selectedFields differ from available fields")
        return {"type": "project", "columns": selected}, {
            c: schema[c] for c in selected
        }
    if short in {"RemoveColumn", "RemoveColumns"}:
        removed = node.get("columnNames", [node.get("columnName")])
        if any(c not in schema for c in removed):
            raise ValueError("removed field is absent from input schema")
        schema = {k: v for k, v in schema.items() if k not in removed}
        return {"type": "project", "columns": list(schema)}, schema
    if short == "RenameColumn":
        old, new = node.get("columnName"), node.get("rename")
        if (
            old not in schema
            or not isinstance(new, str)
            or new in schema
            and new != old
        ):
            raise ValueError("native rename field is missing or collides")
        return {**base, "type": "rename", "mapping": {old: new}}, {
            new if k == old else k: v for k, v in schema.items()
        }
    if short in {"AddColumn", "QuickCalcColumn"}:
        name = node.get("columnName")
        if not isinstance(name, str) or not name:
            raise ValueError("derived column name is missing")
        if (
            short == "QuickCalcColumn"
            and node.get("calcExpressionType") == "RemoveLetters"
        ):
            text = node.get("expression")
            expected = f"REGEXP_REPLACE([{name}], '[:Letter:]', '')"
            if text != expected or name not in schema:
                raise ValueError("unsupported RemoveLetters expression variant")
            expression, dtype = (
                f"REGEXP_REPLACE(CAST({quote(name)} AS VARCHAR), '\\p{{L}}', '', 'g')",
                "string",
            )
        else:
            expression, dtype = translate(node.get("expression"), schema)
        schema[name] = dtype
        return {
            **base,
            "type": "derive",
            "column_name": name,
            "replace": name in columns,
            "expression": expression,
            "expression_language": "sql",
            "original_expression_language": "tableau",
            "original_expression": node.get("expression"),
        }, schema
    if short == "ChangeColumnType":
        expressions = {}
        targets = {
            "string": "VARCHAR",
            "integer": "BIGINT",
            "real": "DOUBLE",
            "date": "DATE",
            "datetime": "TIMESTAMP",
            "boolean": "BOOLEAN",
        }
        if not isinstance(node.get("fields"), dict) or not node["fields"]:
            raise ValueError("native type conversion fields are missing")
        for name, item in node["fields"].items():
            dtype = item.get("type")
            if name not in schema or dtype not in targets:
                raise ValueError("unknown conversion field or native target type")
            if item.get("calc"):
                expression, inferred = translate(item["calc"], schema)
                if inferred != dtype:
                    raise ValueError(
                        "native conversion formula result differs from declared type"
                    )
            else:
                if dtype == "date" and schema[name] not in {"date", "datetime"}:
                    raise ValueError(
                        "native date conversion requires an explicit string input-domain mapping"
                    )
                if dtype == "integer" and schema[name] == "real":
                    expression = f"TRY_CAST(TRUNC({quote(name)}) AS BIGINT)"
                elif dtype == "string" and schema[name] == "boolean":
                    expression = f"CASE WHEN {quote(name)} THEN '1' WHEN NOT {quote(name)} THEN '0' ELSE NULL END"
                elif dtype == "string" and schema[name] in {"real", "date", "datetime"}:
                    raise ValueError(
                        "native string conversion formatting requires explicit mapping"
                    )
                else:
                    expression = f"TRY_CAST({quote(name)} AS {targets[dtype]})"
            expressions[name] = expression
            schema[name] = dtype
        return {**base, "type": "replace_fields", "expressions": expressions}, schema
    if short == "Remap":
        name = node.get("columnName")
        if name not in schema or not isinstance(node.get("values"), dict):
            raise ValueError("native remap field or mapping missing")

        def decode(text):
            try:
                value = json.loads(text)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "remap values must be explicit JSON scalar literals"
                ) from exc
            if isinstance(value, (dict, list)):
                raise ValueError("remap values must be scalar")
            if value is not None and (
                schema[name] == "string"
                and not isinstance(value, str)
                or schema[name] in {"integer", "real"}
                and (type(value) not in {int, float} or not math.isfinite(value))
                or schema[name] not in {"string", "integer", "real"}
            ):
                raise ValueError("remap literal type differs from native field type")
            return value

        seen, cases = set(), []
        for target, sources in node["values"].items():
            output = decode(target)
            for source in sources:
                value = decode(source)
                signature = json.dumps(value)
                if signature in seen:
                    raise ValueError("native remap contains overlapping source groups")
                seen.add(signature)
                condition = (
                    f"{quote(name)} IS NULL"
                    if value is None
                    else f"{quote(name)} = {literal(value)}"
                )
                cases.append(f"WHEN {condition} THEN {literal(output)}")
        return {
            **base,
            "type": "replace_fields",
            "expressions": {
                name: "CASE " + " ".join(cases) + f" ELSE {quote(name)} END"
            },
        }, schema
    if short == "RangeFilter":
        if not isinstance(node.get("ranges"), dict) or len(node["ranges"]) != 1:
            raise ValueError(
                "native range filtering supports one explicit field per operation"
            )
        matches = []
        for name, ranges in node.get("ranges", {}).items():
            if name not in schema or not isinstance(ranges, list):
                raise ValueError("native range field or ranges missing")
            for item in ranges:
                low, high = item.get("startValue"), item.get("endValue")
                if low is None and high is None:
                    matches.append(f"{quote(name)} IS NULL")
                elif low is None or high is None:
                    raise ValueError("one-sided native null range bounds are ambiguous")
                else:

                    def bound(value):
                        if schema[name] in {"integer", "real"}:
                            # JSON integers and native string literals retain
                            # exact evidence. JSON floats have already lost the
                            # original decimal token and must not be reconstructed.
                            if type(value) is int:
                                text = str(value)
                            elif isinstance(value, str):
                                text = value
                            else:
                                raise ValueError(
                                    "native numeric range requires an exact integer or decimal string bound"
                                )
                            if len(text) > 128 or not re.fullmatch(
                                r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",
                                text,
                            ):
                                raise ValueError("invalid exact numeric range bound")
                            try:
                                number = Decimal(text)
                            except InvalidOperation as exc:
                                raise ValueError(
                                    "invalid exact numeric range bound"
                                ) from exc
                            # DuckDB exact decimal literals are bounded to 38
                            # digits; reject evidence beyond that domain before
                            # formatting an exponent into an unbounded string.
                            scale = max(-number.as_tuple().exponent, 0)
                            integer_digits = max(number.adjusted() + 1, 0)
                            if integer_digits + scale > 38:
                                raise ValueError(
                                    "exact numeric range bound exceeds decimal resource domain"
                                )
                            return format(number, "f")
                        return literal(value)

                    matches.append(
                        f"({quote(name)} {'>=' if item.get('includeStart') else '>'} {bound(low)} AND {quote(name)} {'<=' if item.get('includeEnd') else '<'} {bound(high)})"
                    )
        if not matches or not isinstance(node.get("exclude"), bool):
            raise ValueError(
                "native range filter lacks explicit include/exclude semantics"
            )
        matched = " OR ".join(f"({m})" for m in matches)
        # Excluding a non-null group retains null values (Prep value exclusion).
        condition = (
            f"NOT COALESCE(({matched}), FALSE)"
            if node["exclude"]
            else f"COALESCE(({matched}), FALSE)"
        )
        return {**base, "type": "filter", "condition": condition}, schema
    if short == "Aggregate":
        groups, aggregations, result = [], [], {}
        for item in node.get("groupByFields", []):
            name = item.get("columnName")
            if (
                name not in schema
                or item.get("function")
                or item.get("specialFieldType")
            ):
                raise ValueError("unsupported native aggregation grouping")
            alias = item.get("newColumnName") or name
            groups.append(name)
            result[alias] = schema[name]
        for item in node.get("aggregateFields", []):
            name, function = item.get("columnName"), item.get("function")
            alias = item.get("newColumnName") or name
            if (
                name not in schema
                or function not in {"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD"}
                or alias in result
                or item.get("specialFieldType")
            ):
                raise ValueError("unsupported native aggregation field or alias")
            aggregations.append(
                {
                    "name": alias,
                    "expression": f"COUNT(DISTINCT {quote(name)})"
                    if function == "COUNTD"
                    else f"{function}({quote(name)})",
                }
            )
            result[alias] = (
                "integer"
                if function in {"COUNT", "COUNTD"}
                else "real"
                if function == "AVG"
                else schema[name]
            )
        return {
            **base,
            "type": "aggregate",
            "group_by": groups,
            "group_aliases": {
                i["columnName"]: i.get("newColumnName") or i["columnName"]
                for i in node.get("groupByFields", [])
            },
            "aggregations": aggregations,
        }, result
    if short == "Unpivot":
        groups = node.get("unpivotGroups")
        if (
            not isinstance(groups, list)
            or len(groups) != 1
            or not isinstance(groups[0].get("expressions"), list)
            or not groups[0]["expressions"]
        ):
            raise ValueError("native unpivot supports one explicit binding group only")
        values, labels, targets = [], [], None
        missing = []
        for item in groups[0]["expressions"]:
            bindings = item.get("bindings", [])
            if len(bindings) != 2 or [b.get("bindingType") for b in bindings] != [
                "literal",
                "column",
            ]:
                raise ValueError(
                    "native unpivot requires ordered literal and column bindings"
                )
            label, value = bindings
            names = (label.get("newColumnName"), value.get("newColumnName"))
            if (
                not all(isinstance(n, str) and n for n in names)
                or names[0] == names[1]
                or targets is not None
                and targets != names
            ):
                raise ValueError(
                    "native unpivot output aliases differ across expressions"
                )
            targets = names
            if not isinstance(label.get("groupName"), str) or not isinstance(
                value.get("columnName"), str
            ):
                raise ValueError("native unpivot binding value missing")
            labels.append(label["groupName"])
            values.append(value["columnName"])
            if value["columnName"] not in schema:
                missing.append(value["columnName"])
        index = [c for c in columns if c not in values]
        if any(n in index for n in targets):
            raise ValueError("native unpivot aliases collide with retained fields")
        types = {schema[c] for c in values if c in schema}
        if len(types) > 1:
            raise ValueError("native unpivot value types differ")
        result = {c: schema[c] for c in index}
        result.update({targets[0]: "string", targets[1]: next(iter(types), "unknown")})
        return {
            "type": "pivot",
            "direction": "columns_to_rows",
            "index": index,
            "values": values,
            "labels": labels,
            "names_to": targets[0],
            "values_to": targets[1],
            "include_nulls": True,
            "native_missing_fields": missing,
            "semantic_issues": [
                {"code": "native_pivot_field_missing", "fields": missing}
            ]
            if missing
            else [],
            "translation": "native_single_unpivot_include_nulls_v1",
        }, result
    if short in {"WriteToHyper", "WriteToCsv", "PublishExtract", "Output"}:
        return {"type": "output"}, schema
    raise ValueError(f"unsupported native Prep operation {short!r}")


def declared_schema(node, ports):
    """Keep declared output fields on local semantic failure, never executable SQL."""
    if len(ports) != 1 or next(iter(ports.values())) is None:
        return None
    schema = dict(next(iter(ports.values())))
    short = str(node.get("nodeType", "")).split(".")[-1]
    if short == "ChangeColumnType" and isinstance(node.get("fields"), dict):
        for name, item in node["fields"].items():
            if name not in schema or not isinstance(item, dict):
                return None
            schema[name] = item.get("type", "unknown")
        return schema
    if short in {"AddColumn", "QuickCalcColumn"} and isinstance(
        node.get("columnName"), str
    ):
        schema[node["columnName"]] = "unknown"
        return schema
    return None
