"""Bounded, offline readers for Tableau definitions, without extracting data files.

Native formats are version-sensitive. Unknown operations remain visible and are
never treated as a successfully translated query.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as SafeET

MAX_DEFINITION_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 2048
SECRET_KEYS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "accesstoken",
    "refreshtoken",
    "secret",
    "clientsecret",
    "authorization",
    "cookie",
    "username",
    "user",
    "personalaccesstokensecret",
    "credentials",
    "credential",
    "connectionpassword",
}
SAMPLE_KEYS = {"samplerows", "sampledata", "previewdata", "rowdata", "profilestats"}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def redact(value: Any) -> Any:
    """Remove credential fields and URI userinfo, retaining connection identity."""
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if _key(str(k)) in SECRET_KEYS else redact(v)
            for k, v in value.items()
            if _key(str(k)) not in SAMPLE_KEYS
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        try:
            parsed = urlsplit(value)
            return urlunsplit(
                (parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", "")
            )
        except ValueError:
            return "[REDACTED URI]"
    return value


def _definition(payload: bytes, kind: str) -> bytes:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("definition must be non-empty bytes")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("native archive exceeds size limit")
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        if len(payload) > MAX_DEFINITION_BYTES:
            raise ValueError("definition exceeds size limit")
        return payload
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise ValueError("native archive has too many members")
            candidates = []
            suffix = {"workbook": ".twb", "datasource": ".tds", "flow": ".tfl"}[kind]
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    raise ValueError("unsafe native archive member path")
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("native archive contains a symbolic link")
                match = path.suffix.lower() == suffix or (
                    kind == "flow" and path.name == "flow"
                )
                if member.is_dir() or not match:
                    continue
                if member.flag_bits & 1:
                    raise ValueError("encrypted native definition is unsupported")
                if (
                    member.file_size > MAX_DEFINITION_BYTES
                    or member.file_size > max(member.compress_size, 1) * 200
                ):
                    raise ValueError("native definition exceeds decompression limit")
                candidates.append(member)
            if len(candidates) != 1:
                raise ValueError(
                    f"expected exactly one {kind} definition, found {len(candidates)}"
                )
            with archive.open(candidates[0]) as source:
                content = source.read(MAX_DEFINITION_BYTES + 1)
            if len(content) > MAX_DEFINITION_BYTES:
                raise ValueError("definition exceeds size limit")
            return content
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise ValueError("invalid native archive") from exc


def _xml(payload: bytes, kind: str) -> ET.Element:
    definition = _definition(payload, kind)
    try:
        root = SafeET.fromstring(
            definition, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except Exception as exc:
        raise ValueError(f"invalid or unsafe {kind} XML") from exc
    # Namespaced inputs are accepted without losing their original element names
    # in the source document. Tableau normally uses unnamespaced definitions.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
        element.attrib = redact(element.attrib)
        if _key(element.tag) in SECRET_KEYS:
            element.text = "[REDACTED]"
    return root


def _node(element: ET.Element) -> dict[str, Any]:
    result: dict[str, Any] = {"tag": element.tag, "attributes": dict(element.attrib)}
    if element.text and element.text.strip():
        result["text"] = element.text.strip()
    if len(element):
        result["children"] = [_node(child) for child in element]
    return result


def _result() -> dict[str, Any]:
    return {
        "assets": [],
        "dependencies": [],
        "queries": [],
        "coverage": [],
        "documents": {},
    }


def _asset(
    out: dict, asset_id: str, kind: str, name: str, definition: dict, **source: Any
) -> dict:
    value = {
        "id": asset_id,
        "kind": kind,
        "name": name,
        "source": source,
        "definition": definition,
        "status": "ok",
    }
    out["assets"].append(value)
    return value


def _edge(out: dict, source: str, target: str, relation: str = "depends_on") -> None:
    edge = {"from": source, "to": target, "relation": relation}
    if edge not in out["dependencies"]:
        out["dependencies"].append(edge)


def _coverage(
    out: dict, asset_id: str, code: str, message: str, status: str = "unsupported"
) -> None:
    out["coverage"].append(
        {"asset_id": asset_id, "status": status, "code": code, "message": message}
    )


def _identifier(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:20]


def _dataset(out: dict, element: ET.Element, asset_id: str, **source: Any) -> None:
    columns = []
    for column in element.findall("./column"):
        calculation = column.find("./calculation")
        columns.append(
            {
                **dict(column.attrib),
                **(
                    {"calculation": dict(calculation.attrib)}
                    if calculation is not None
                    else {}
                ),
            }
        )
    connections = [dict(item.attrib) for item in element.findall(".//connection")]
    repositories = [
        dict(item.attrib) for item in element.findall(".//repository-location")
    ]
    definition = {
        "columns": columns,
        "connections": connections,
        "relations": [_node(item) for item in element.findall("./connection/relation")],
        "logical_model": [_node(item) for item in element.findall("./object-graph")],
        "filters": [_node(item) for item in element.findall("./filter")],
        "extract": [_node(item) for item in element.findall("./extract")],
        "repository_locations": repositories,
    }
    if repositories:
        source["published_id"] = repositories[0].get("id")
        source["content_url"] = repositories[0].get("derived-from") or repositories[
            0
        ].get("path")
    _asset(
        out,
        asset_id,
        "dataset",
        element.get("caption") or element.get("name") or asset_id,
        definition,
        **source,
    )
    for index, relation in enumerate(element.findall(".//relation")):
        if relation.get("type") == "text" and (relation.text or "").strip():
            text = relation.text.strip()
            query_id = f"query:{_identifier(asset_id, str(index), text)}"
            out["queries"].append(
                {
                    "id": query_id,
                    "asset_id": asset_id,
                    "origin": "source",
                    "language": "sql",
                    "text": text,
                    "status": "ok",
                }
            )
        elif relation.get("type") == "table":
            table = relation.get("table") or relation.get("name")
            connection = relation.get("connection")
            external_id = (
                f"external:{_identifier(asset_id, connection or '', table or '')}"
            )
            if not any(item["id"] == external_id for item in out["assets"]):
                _asset(
                    out,
                    external_id,
                    "external_source",
                    table or "unknown table",
                    {
                        "table": table,
                        "connection": connection,
                        "connections": connections,
                        "etl_definition": None,
                    },
                    **source,
                )
            _edge(out, asset_id, external_id)
            _coverage(
                out,
                external_id,
                "external_etl_unresolved",
                "Physical table identity is known; its upstream ETL definition has not been supplied.",
                "partial",
            )
    for column in columns:
        formula = column.get("calculation", {}).get("formula", "")
        if _complex_formula(formula):
            # A dataset can contain unused complex calculations. Record the
            # field-level caveat without blocking every consumer of that source.
            _coverage(
                out,
                asset_id,
                "tableau_calculation",
                f"Field {column.get('name')} requires explicit DOSI semantic mapping; its native formula is retained.",
                "partial",
            )
            out["coverage"][-1]["field_id"] = column.get("name")


def _complex_formula(formula: str) -> bool:
    return bool(
        re.search(
            r"\{\s*(?:FIXED|INCLUDE|EXCLUDE)\b|\b(?:WINDOW_\w+|LOOKUP|RUNNING_\w+|INDEX|SIZE|FIRST|LAST|RANK\w*)\s*\(",
            formula,
            re.I,
        )
    )


def parse_workbook(
    payload: bytes, *, workbook_id: str, workbook_name: str | None = None
) -> dict:
    root = _xml(payload, "workbook")
    if root.tag != "workbook":
        raise ValueError("expected workbook XML root")
    out = _result()
    wb_id = f"workbook:{workbook_id}"
    _asset(
        out,
        wb_id,
        "workbook",
        workbook_name or root.get("name") or workbook_id,
        {
            "version": root.get("version"),
            "parameters": [
                _node(item)
                for item in root.findall("./datasources/datasource[@name='Parameters']")
            ],
        },
        workbook_luid=workbook_id,
    )
    datasets: dict[str, str] = {}
    for element in root.findall("./datasources/datasource"):
        name = element.get("name", "")
        if name == "Parameters":
            continue
        dataset_id = f"dataset:{workbook_id}:{name}"
        datasets[name] = dataset_id
        _dataset(out, element, dataset_id, workbook_luid=workbook_id, native_name=name)
        _edge(out, wb_id, dataset_id, "contains")
    charts: dict[str, str] = {}
    for worksheet in root.findall("./worksheets/worksheet"):
        name = worksheet.get("name", "")
        chart_id = f"chart:{workbook_id}:{name}"
        charts[name] = chart_id
        table = worksheet.find("./table")
        columns = worksheet.findall(".//datasource-dependencies/column")
        definition = {
            "worksheet": _node(worksheet),
            "rows": table.findtext("rows") if table is not None else None,
            "columns": table.findtext("cols") if table is not None else None,
            "marks": [dict(item.attrib) for item in worksheet.findall(".//mark")],
            "encodings": [_node(item) for item in worksheet.findall(".//encodings")],
            "filters": [_node(item) for item in worksheet.findall(".//filter")],
            "sorts": [_node(item) for item in worksheet.findall(".//sort")],
            "calculations": [
                _node(item) for item in columns if item.find("calculation") is not None
            ],
            "query_status": "runtime_sql_unavailable",
            "filter_order": "tableau_native",
        }
        chart = _asset(
            out,
            chart_id,
            "chart",
            name,
            definition,
            workbook_luid=workbook_id,
            native_name=name,
        )
        for item in worksheet.findall(".//datasource") + worksheet.findall(
            ".//datasource-dependencies"
        ):
            ref = item.get("name") or item.get("datasource")
            if ref in datasets:
                _edge(out, chart_id, datasets[ref])
            elif ref and ref != "Parameters":
                missing_id = f"dataset:{workbook_id}:{ref}"
                _edge(out, chart_id, missing_id)
                _coverage(
                    out,
                    chart_id,
                    "dataset_missing",
                    f"Referenced dataset {ref!r} was not present in the workbook.",
                    "partial",
                )
        for item in worksheet.findall(".//calculation"):
            if (
                _complex_formula(item.get("formula", ""))
                or item.get("class") == "table"
            ):
                chart["status"] = "unsupported"
                _coverage(
                    out,
                    chart_id,
                    "tableau_calculation",
                    "LOD/table calculations require an explicit equivalent DOSI metric; native calculation and filter order are retained.",
                )
        binding_text = " ".join(
            value
            for item in worksheet.iter()
            if item.tag not in {"datasource-dependencies", "column", "calculation"}
            for value in [*(str(v) for v in item.attrib.values()), item.text or ""]
        )
        for dataset in out["assets"]:
            if dataset["kind"] != "dataset" or not any(
                edge["from"] == chart_id and edge["to"] == dataset["id"]
                for edge in out["dependencies"]
            ):
                continue
            for column in dataset["definition"]["columns"]:
                formula = column.get("calculation", {}).get("formula", "")
                native_field = column.get("name", "").strip("[]")
                if (
                    native_field
                    and native_field in binding_text
                    and _complex_formula(formula)
                ):
                    chart["status"] = "unsupported"
                    _coverage(
                        out,
                        chart_id,
                        "tableau_calculation",
                        f"Referenced field {column.get('name')} requires explicit DOSI semantic mapping.",
                    )
        if worksheet.findall(".//table-calc") or worksheet.findall(
            ".//table-calculation"
        ):
            chart["status"] = "unsupported"
            _coverage(
                out,
                chart_id,
                "table_calculation",
                "Native table-calculation addressing and partitioning require explicit semantic mapping.",
            )
        _edge(out, wb_id, chart_id, "contains")
    for dashboard in root.findall("./dashboards/dashboard"):
        name = dashboard.get("name", "")
        dashboard_id = f"dashboard:{workbook_id}:{name}"
        refs = list(
            dict.fromkeys(
                item.get("name")
                for item in dashboard.findall(".//zone")
                if item.get("name") in charts
            )
        )
        _asset(
            out,
            dashboard_id,
            "dashboard",
            name,
            {
                "layout": _node(dashboard),
                "worksheets": [charts[ref] for ref in refs],
                "actions": [_node(item) for item in root.findall("./actions/action")],
                "windows": [
                    _node(item)
                    for item in root.findall("./windows/window")
                    if item.get("name") == name
                ],
            },
            workbook_luid=workbook_id,
            native_name=name,
        )
        _edge(out, wb_id, dashboard_id, "contains")
        for ref in refs:
            _edge(out, dashboard_id, charts[ref], "contains")
    out["documents"][f"workbook-{_identifier(workbook_id)}.twb"] = ET.tostring(
        root, encoding="unicode"
    )
    return out


def parse_datasource(payload: bytes, *, datasource_id: str) -> dict:
    root = _xml(payload, "datasource")
    if root.tag != "datasource":
        raise ValueError("expected datasource XML root")
    out = _result()
    _dataset(
        out,
        root,
        f"dataset:{datasource_id}",
        luid=datasource_id,
        native_name=root.get("name"),
    )
    out["documents"][f"datasource-{_identifier(datasource_id)}.tds"] = ET.tostring(
        root, encoding="unicode"
    )
    return out


def _prep_operation(node: dict) -> dict:
    node_type = str(node.get("nodeType") or node.get("type") or "")
    short = node_type.rsplit(".", 1)[-1]
    supported = {
        "LoadSql": "source",
        "LoadExcel": "source",
        "Input": "source",
        "Source": "source",
        "SimpleJoin": "join",
        "Join": "join",
        "Union": "union",
        "Aggregate": "aggregate",
        "Pivot": "pivot",
        "AddColumn": "derive",
        "FilterOperation": "filter",
        "RenameColumn": "rename",
        "RemoveColumns": "project",
        "WriteToHyper": "output",
        "WriteToCsv": "output",
        "PublishExtract": "output",
        "Output": "output",
        "Clean": "clean",
        "Container": "container",
        "SuperJoin": "container",
        "SuperAggregate": "container",
        "SuperPivot": "container",
    }
    operation = {
        "type": supported.get(short, "unsupported"),
        "native_type": node_type,
        "native": node,
    }
    # These are explicit normalized fields, never speculative translations of a
    # native Tableau formula into SQL. Native variants retain all original keys.
    for key in (
        "table",
        "sql",
        "columns",
        "condition",
        "join_type",
        "group_by",
        "aggregations",
        "expression",
        "name",
        "mapping",
        "values",
        "index",
        "names_to",
        "values_to",
        "mode",
        "expression_language",
        "source_dialect",
        "projection",
        "alignment",
        "column_name",
        "direction",
        "include_nulls",
        "write_mode",
        "changes",
    ):
        if key in node:
            operation[key] = copy.deepcopy(node[key])
    if operation["type"] == "source" and isinstance(node.get("customSql"), str):
        operation["sql"] = node["customSql"]
    if operation["type"] == "clean":
        # Native cleaning can encode replacements, grouping and null-handling in
        # many schema variants. Only an explicit empty change list proves that
        # passing the input through is correct.
        if node.get("changes") != []:
            operation["type"] = "unsupported"
    return operation


def parse_flow(payload: bytes, *, flow_id: str) -> dict:
    try:
        document = json.loads(_definition(payload, "flow"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid Prep flow JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("expected Prep flow object")
    document = redact(document)
    flow = document.get("flow", document)
    if not isinstance(flow, dict):
        raise ValueError("invalid Prep flow root")
    out = _result()
    raw_nodes = flow.get("nodes")
    if not isinstance(raw_nodes, (dict, list)):
        raise ValueError("Prep flow nodes are missing or unsupported")
    flow_asset = _asset(
        out,
        f"transform:{flow_id}",
        "transform",
        str(flow.get("name") or flow_id),
        {
            "operation": {"type": "flow"},
            "parameters": flow.get("parameters", {}),
            "connections": flow.get("connections", {}),
            "version": flow.get("version"),
        },
        flow_luid=flow_id,
    )
    known: dict[str, str] = {}
    nodes: list[tuple[str, dict]] = []

    def collect(values: dict | list, prefix: str = "") -> None:
        items = (
            values.items()
            if isinstance(values, dict)
            else ((str(i), value) for i, value in enumerate(values))
        )
        for key, value in items:
            if not isinstance(value, dict):
                raise ValueError("Prep flow node must be an object")
            native_id = str(value.get("id") or key)
            scoped = prefix + native_id
            if scoped in known:
                raise ValueError("duplicate Prep flow node identity")
            known[scoped] = f"transform:{flow_id}:{scoped}"
            nodes.append((scoped, value))
            nested = (
                value.get("loomContainer", {}).get("nodes")
                if isinstance(value.get("loomContainer"), dict)
                else None
            )
            if isinstance(nested, (dict, list)):
                collect(nested, scoped + "/")

    collect(raw_nodes)
    for key, value in nodes:
        asset_id = known[key]
        operation = _prep_operation(value)
        definition = {
            "operation": operation,
            "inputs": [],
            "parameters": value.get("parameters", {}),
            "incremental": value.get("incrementalRefresh") or value.get("incremental"),
        }
        asset = _asset(
            out,
            asset_id,
            "transform",
            str(value.get("name") or key),
            definition,
            flow_luid=flow_id,
            native_id=key,
        )
        _edge(out, flow_asset["id"], asset_id, "contains")
        parent = key.rsplit("/", 1)[0] if "/" in key else None
        if parent:
            _edge(out, known[parent], asset_id, "contains")
        scope = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
        for next_node in value.get("nextNodes", []):
            target = (
                next_node.get("nextNodeId") or next_node.get("id")
                if isinstance(next_node, dict)
                else next_node
            )
            target_key = scope + str(target)
            target_id = known.get(target_key, f"transform:{flow_id}:{target_key}")
            _edge(out, target_id, asset_id)
            if target_key not in known:
                _coverage(
                    out,
                    asset_id,
                    "flow_node_missing",
                    f"Downstream node {target!r} is absent.",
                    "partial",
                )
        inputs = value.get("inputs", [])
        if isinstance(inputs, dict) and set(inputs) == {"left", "right"}:
            inputs = [inputs["left"], inputs["right"]]
        explicit_inputs = []
        if isinstance(inputs, list):
            for input_ref in inputs:
                input_key = (
                    input_ref.get("nodeId") or input_ref.get("id")
                    if isinstance(input_ref, dict)
                    else input_ref
                )
                if input_key is not None:
                    resolved = known.get(
                        scope + str(input_key),
                        f"transform:{flow_id}:{scope}{input_key}",
                    )
                    explicit_inputs.append(resolved)
                    _edge(
                        out,
                        asset_id,
                        resolved,
                    )
        definition["inputs"] = explicit_inputs
        definition["input_order"] = "explicit" if explicit_inputs else "topology_only"
        if operation["type"] == "join" and (
            len(explicit_inputs) != 2 or len(set(explicit_inputs)) != 2
        ):
            definition["input_order"] = "unresolved"
        sql = value.get("customSql") or value.get("sql")
        if isinstance(sql, str) and sql.strip():
            out["queries"].append(
                {
                    "id": f"query:{_identifier(asset_id, sql)}",
                    "asset_id": asset_id,
                    "origin": "source",
                    "language": "sql",
                    "text": sql,
                    "status": "ok",
                }
            )
        if operation["type"] in {"unsupported", "container"}:
            asset["status"] = "unsupported"
            _coverage(
                out,
                asset_id,
                "prep_operation_requires_mapping",
                f"Preserved native {operation['native_type']!r}; execution order/semantics need explicit mapping before SQL generation.",
            )
    assets = {item["id"]: item for item in out["assets"]}
    for edge in out["dependencies"]:
        if edge["relation"] == "depends_on" and edge["from"] in assets:
            inputs = assets[edge["from"]]["definition"]["inputs"]
            if edge["to"] not in inputs:
                inputs.append(edge["to"])
    for asset in assets.values():
        definition = asset["definition"]
        if definition.get("operation", {}).get("type") != "join":
            continue
        inputs = definition["inputs"]
        if (
            definition["input_order"] != "explicit"
            or len(inputs) != 2
            or len(set(inputs)) != 2
        ):
            asset["status"] = "unsupported"
            _coverage(
                out,
                asset["id"],
                "join_input_order_unresolved",
                "Join left/right inputs require exactly two distinct explicitly ordered inputs; nextNodes only establishes topology.",
            )
    out["documents"][f"flow-{_identifier(flow_id)}.tfl"] = json.dumps(
        document, ensure_ascii=False, indent=2
    )
    return out
