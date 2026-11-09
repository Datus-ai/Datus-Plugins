"""Compose read-only REST/Metadata/native sources into independently verifiable bundles."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .bundle import digest, json_bytes, redact, workspace_path, write_bundle
from .errors import PluginError, UsageError
from .native import parse_datasource, parse_flow, parse_workbook


def discover_dashboards(
    client: Any,
    *,
    project_ids: list[str] | None = None,
    workbook_ids: set[str] | None = None,
) -> tuple[list[dict], dict]:
    dashboards: list[dict] = []
    parsed: dict[str, dict] = {}
    for book in client.list_resources("workbooks"):
        if project_ids and book.get("project_id") not in project_ids:
            continue
        if workbook_ids is not None and book["id"] not in workbook_ids:
            continue
        identifier = book["id"]
        try:
            document = parse_workbook(
                client.download("workbooks", identifier),
                workbook_id=identifier,
                workbook_name=book.get("name"),
            )
            parsed[identifier] = {"book": book, "document": document}
            for asset in document["assets"]:
                if asset["kind"] == "dashboard":
                    dashboards.append(
                        {
                            "id": asset["id"],
                            "name": asset["name"],
                            "workbook_id": identifier,
                            "project_id": book.get("project_id"),
                            "status": asset["status"],
                        }
                    )
        except Exception as exc:
            # Retain the inaccessible workbook in the discovery result, but never pretend it is a dashboard.
            parsed[identifier] = {"book": book, "error": type(exc).__name__}
    return dashboards, parsed


def _reachable(starts: set[str], edges: list[dict]) -> set[str]:
    adjacent: dict[str, set] = defaultdict(set)
    for edge in edges:
        adjacent[edge["from"]].add(edge["to"])
    seen = set(starts)
    pending = list(starts)
    while pending:
        for target in adjacent[pending.pop()]:
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen


def _cycles(assets: list[dict], edges: list[dict]) -> list[str]:
    adjacent: dict[str, list] = defaultdict(list)
    for edge in edges:
        adjacent[edge["from"]].append(edge["to"])
    active, done, cycles = set(), set(), set()
    for asset in assets:
        node = asset["id"]
        if node in done:
            continue
        active.add(node)
        stack = [(node, iter(adjacent[node]))]
        while stack:
            current, dependencies = stack[-1]
            target = next(dependencies, None)
            if target is None:
                active.remove(current)
                done.add(current)
                stack.pop()
            elif target in active:
                cycles.add(target)
            elif target not in done:
                active.add(target)
                stack.append((target, iter(adjacent[target])))
    return sorted(cycles)


def sync_sources(
    client: Any,
    *,
    dashboard_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    all_dashboards: bool = False,
    output_root: str = "bi_sources/tableau",
    overwrite: bool = False,
    external_sources: list[str] | None = None,
    dialect: str = "duckdb",
    source_bindings: dict | None = None,
) -> dict:
    if sum(bool(x) for x in (dashboard_ids, project_ids, all_dashboards)) != 1:
        raise UsageError("select exactly one of --dashboard-id, --project-id or --all")
    source = {
        "server_url": client.settings.server_url,
        "site_content_url": client.settings.site_content_url,
    }
    selection = {
        "dashboard_ids": sorted(set(dashboard_ids or [])),
        "project_ids": sorted(set(project_ids or [])),
        "all": all_dashboards,
    }
    key = digest(json_bytes({"source": source, "selection": selection}))[:16]
    target = workspace_path(output_root) / key
    if target.exists() and not overwrite:
        raise UsageError(f"snapshot exists at {target}; use --overwrite")
    selected_workbooks = None
    if dashboard_ids:
        if any(
            not value.startswith("dashboard:") or len(value.split(":", 2)) != 3
            for value in dashboard_ids
        ):
            raise UsageError(
                "use scoped dashboard identifiers returned by dashboards list"
            )
        selected_workbooks = {value.split(":", 2)[1] for value in dashboard_ids}
    rows, discovered = discover_dashboards(
        client, project_ids=project_ids, workbook_ids=selected_workbooks
    )
    chosen = {
        row["id"] for row in rows if not dashboard_ids or row["id"] in dashboard_ids
    }
    if dashboard_ids and (unknown := set(dashboard_ids) - chosen):
        raise UsageError(
            "dashboard IDs were not found or their workbook definitions are not downloadable: "
            + ", ".join(sorted(unknown))
        )
    if not chosen and not any("error" in item for item in discovered.values()):
        raise UsageError("selection contains no dashboards")

    assets: dict[str, dict] = {}
    queries: dict[str, dict] = {}
    edges: list[dict] = []
    coverage: list[dict] = []
    extra: dict[str, bytes] = {}
    fetched = set()
    datasource_bindings = []
    datasource_lineages = {}
    flow_bindings = []
    failures = 0

    def issue(asset_id, code, message, *, status="error"):
        coverage.append(
            {"asset_id": asset_id, "status": status, "code": code, "message": message}
        )

    def add_asset(asset):
        identifier = asset["id"]
        if identifier in assets and assets[identifier] != asset:
            raise PluginError("conflicting definitions for the same source asset")
        assets[identifier] = redact(asset)

    def merge(parsed, scope_id):
        for asset in parsed.get("assets", []):
            add_asset(asset)
        edges.extend(parsed.get("dependencies", []))
        for query in parsed.get("queries", []):
            identifier = query["id"]
            if identifier in queries and queries[identifier] != query:
                raise PluginError("conflicting source query definitions")
            queries[identifier] = query.copy()
        coverage.extend(parsed.get("coverage", []))
        for name, content in parsed.get("documents", {}).items():
            # Native parser owns redaction; only text definitions, never original archive bytes.
            suffix = ".json" if name.endswith(".json") else ".xml"
            filename = f"native/{digest((scope_id + ':' + name).encode())[:24]}{suffix}"
            extra[filename] = content.encode()

    selected_books = {row["workbook_id"] for row in rows if row["id"] in chosen}
    for identifier, item in discovered.items():
        if "error" in item:
            # --all/project selection must report workbooks it could not inspect.
            if not dashboard_ids:
                workbook_id = f"workbook:{identifier}"
                add_asset(
                    {
                        "id": workbook_id,
                        "kind": "workbook",
                        "name": item["book"].get("name", identifier),
                        "source": {"luid": identifier},
                        "definition": {},
                        "status": "error",
                    }
                )
                issue(
                    workbook_id,
                    "native_download_failed",
                    "Workbook definition is unavailable; dashboard inventory is incomplete",
                )
                failures += 1
            continue
        if identifier not in selected_books:
            continue
        parsed = item["document"]
        # Retain only selected dashboards plus their upstream graph. A sibling dashboard is not selected implicitly.
        selected_here = {a["id"] for a in parsed["assets"] if a["id"] in chosen}
        partial_workbook = selected_here != {
            asset["id"] for asset in parsed["assets"] if asset["kind"] == "dashboard"
        }
        keep = _reachable(selected_here, parsed.get("dependencies", []))
        keep.update(a["id"] for a in parsed["assets"] if a["kind"] == "workbook")
        merge(
            {
                **parsed,
                # A complete native workbook also contains unselected dashboards
                # and their queries. Structured selected assets retain their dependencies.
                "documents": {} if partial_workbook else parsed.get("documents", {}),
                "assets": [a for a in parsed["assets"] if a["id"] in keep],
                "dependencies": [
                    e
                    for e in parsed.get("dependencies", [])
                    if e["from"] in keep and e["to"] in keep
                ],
                "queries": [
                    q for q in parsed.get("queries", []) if q["asset_id"] in keep
                ],
                "coverage": [
                    c for c in parsed.get("coverage", []) if c["asset_id"] in keep
                ],
            },
            f"workbook:{identifier}",
        )
        workbook_id = f"workbook:{identifier}"
        charts_here = [
            a["id"]
            for a in parsed["assets"]
            if a["kind"] == "chart" and a["id"] in keep
        ]

        def book_issue(code, message, status="error"):
            issue(workbook_id, code, message, status=status)
            for chart_id in charts_here:
                issue(chart_id, code, message, status=status)

        if workbook_id not in assets:
            add_asset(
                {
                    "id": workbook_id,
                    "kind": "workbook",
                    "name": item["book"].get("name", identifier),
                    "source": {"luid": identifier},
                    "definition": {},
                    "status": "ok",
                }
            )
        if partial_workbook:
            issue(
                workbook_id,
                "selection_scoped_source_copy",
                "Full native workbook and raw workbook-level Metadata copy omitted for a partial dashboard selection; selected structured definitions and shared dependencies are retained.",
                status="ok",
            )
        # Keep REST identity and Metadata identity separate.
        try:
            lineage = client.workbook_lineage(identifier)
            if not partial_workbook:
                extra[f"metadata/{digest(identifier.encode())[:24]}.json"] = json_bytes(
                    redact(lineage)
                )
            assets[workbook_id]["source"]["metadata_id"] = lineage.get(
                "workbook", {}
            ).get("id")
        except Exception:
            book_issue(
                "metadata_unavailable",
                "Metadata lineage is unavailable or incomplete; source definitions do not prove full upstream coverage",
            )
            failures += 1
            continue
        try:
            for row in client.workbook_custom_sql(identifier):
                query_id = "query:metadata:" + str(row["id"])
                text = row.get("query")
                usable = isinstance(text, str) and bool(text.strip())
                unsupported = bool(row.get("isUnsupportedCustomSql"))
                if partial_workbook and text not in {
                    query["text"]
                    for query in queries.values()
                    if query["origin"] == "source" and query["language"] == "sql"
                }:
                    # A workbook-level association does not establish that this SQL
                    # belongs to the selected dashboard. Never export an unscoped SQL body.
                    book_issue(
                        "custom_sql_scope_unresolved",
                        "Workbook-level custom SQL was omitted because its selected-dashboard dependency could not be established.",
                        status="partial",
                    )
                    continue
                add_asset(
                    {
                        "id": query_id,
                        "kind": "query",
                        "name": row.get("name") or str(row["id"]),
                        "source": {
                            "metadata_id": row["id"],
                            "database": redact(row.get("database") or {}),
                        },
                        "definition": {"is_unsupported_custom_sql": unsupported},
                        "status": "ok" if usable and not unsupported else "partial",
                    }
                )
                edges.append(
                    {"from": workbook_id, "to": query_id, "relation": "custom_sql"}
                )
                # Metadata proves workbook-level use, not the exact consuming sheet.
                # Conservatively expose the query and its coverage to this selection's
                # charts only; a chart -> workbook edge would introduce contains cycles.
                for chart_id in charts_here:
                    edges.append(
                        {"from": chart_id, "to": query_id, "relation": "custom_sql"}
                    )
                if usable:
                    queries[query_id] = {
                        "id": query_id,
                        "asset_id": query_id,
                        "origin": "source",
                        "language": "sql",
                        "text": text,
                        "status": "partial" if unsupported else "ok",
                    }
                if unsupported or not usable:
                    issue(
                        query_id,
                        "custom_sql_lineage_incomplete"
                        if usable
                        else "custom_sql_coverage",
                        "Tableau Catalog cannot fully resolve Custom SQL lineage; verify the exact native SQL and physical connection"
                        if usable
                        else "Custom SQL text is hidden or unavailable",
                        status="partial",
                    )
        except Exception:
            book_issue(
                "custom_sql_metadata_unavailable",
                "Metadata custom SQL could not be fully queried; native SQL alone does not prove complete coverage",
            )
            failures += 1
        for row in lineage.get("tables", []):
            table_id = "external_source:table:" + str(row["id"])
            add_asset(
                {
                    "id": table_id,
                    "kind": "external_source",
                    "name": row.get("name") or str(row["id"]),
                    "source": {"metadata_id": row["id"]},
                    "definition": redact(row),
                    "status": "partial",
                }
            )
            edges.append(
                {"from": workbook_id, "to": table_id, "relation": "upstream_table"}
            )
            # Metadata only establishes workbook-level use. Keep its unresolved
            # producing definition visible to this selection's actual consumers.
            for chart_id in charts_here:
                edges.append(
                    {"from": chart_id, "to": table_id, "relation": "upstream_table"}
                )
            issue(
                table_id,
                "external_definition_required",
                "Physical table identity is known; its producing ETL definition has not been supplied",
                status="partial",
            )
        for group, parser, root_kind in (
            ("datasources", parse_datasource, "dataset"),
            ("flows", parse_flow, "transform"),
        ):
            for row in lineage.get(group, []):
                luid = row.get("luid")
                if not luid:
                    book_issue(
                        "hidden_upstream_identity",
                        f"An upstream {group} identity was hidden",
                        status="partial",
                    )
                    continue
                upstream_id = (
                    f"{'dataset' if group == 'datasources' else 'transform'}:{luid}"
                )
                if upstream_id not in assets:
                    source_info = {"luid": luid, "metadata_id": row.get("id")}
                    try:
                        resource = client.get_resource(group, luid)
                        source_info["content_url"] = resource.get("content_url")
                        source_info["connections"] = resource.get("connections", [])
                    except Exception:
                        book_issue(
                            "upstream_rest_identity_unavailable",
                            f"Upstream {group} REST connection identity could not be read",
                            status="partial",
                        )
                    add_asset(
                        {
                            "id": upstream_id,
                            "kind": root_kind,
                            "name": row.get("name") or luid,
                            "source": source_info,
                            "definition": {},
                            "status": "ok",
                        }
                    )
                edges.append(
                    {
                        "from": workbook_id,
                        "to": upstream_id,
                        "relation": "upstream_" + group,
                    }
                )
                local_sources = [
                    a
                    for a in parsed["assets"]
                    if a["kind"] == "dataset" and a["id"] in keep
                ]
                if group == "datasources":
                    # Resolve after every identity has been read, so ambiguous
                    # references cannot bind to whichever source was fetched first.
                    datasource_bindings.append(
                        (
                            workbook_id,
                            charts_here,
                            [a["id"] for a in local_sources],
                            upstream_id,
                        )
                    )
                    if lineage.get("flows") and luid not in datasource_lineages:
                        # Workbook shortcuts cannot assign a flow to a datasource.
                        # Keep the independently scoped response as source evidence.
                        datasource_lineages[luid] = None
                        try:
                            scoped = client.datasource_lineage(luid)
                            identity = scoped.get("datasource", {})
                            if (
                                identity.get("luid") != luid
                                or not identity.get("id")
                                or identity["id"]
                                != assets[upstream_id]["source"].get("metadata_id")
                                or not isinstance(scoped.get("flows"), list)
                                or not isinstance(scoped.get("tables"), list)
                            ):
                                raise PluginError(
                                    "datasource lineage identity is unavailable"
                                )
                            datasource_lineages[luid] = scoped
                            extra[
                                f"metadata/datasource-{digest(luid.encode())[:24]}.json"
                            ] = json_bytes(redact(scoped))
                        except Exception:
                            issue(
                                upstream_id,
                                "datasource_metadata_unavailable",
                                "Datasource-scoped Metadata lineage could not be verified",
                                status="partial",
                            )
                else:
                    flow_bindings.append((workbook_id, charts_here, upstream_id))
                # A shared source is downloaded once even when several dashboards/workbooks use it.
                if (group, luid) in fetched:
                    continue
                fetched.add((group, luid))
                try:
                    document = parser(
                        client.download(group, luid),
                        **{
                            "datasource_id"
                            if group == "datasources"
                            else "flow_id": luid
                        },
                    )
                    # Merge native root metadata without changing identity or discarding GraphQL source fields.
                    root = next(
                        (a for a in document["assets"] if a["id"] == upstream_id), None
                    )
                    if root:
                        root["source"] = {
                            **root["source"],
                            **assets[upstream_id]["source"],
                        }
                        assets.pop(upstream_id)
                    merge(document, upstream_id)
                    for native_asset in document["assets"]:
                        if native_asset["id"] != upstream_id:
                            edges.append(
                                {
                                    "from": upstream_id,
                                    "to": native_asset["id"],
                                    "relation": "definition_member",
                                }
                            )
                except Exception:
                    assets[upstream_id]["status"] = "error"
                    issue(
                        upstream_id,
                        "native_download_failed",
                        f"Upstream {group} definition could not be read or parsed",
                    )
                    failures += 1

    # Explicit local source definitions are evidence, never executed/imported as Python or SQL.
    for value in external_sources or []:
        path = workspace_path(value)
        if (
            not path.is_file()
            or path.suffix.lower() not in {".sql", ".json"}
            or path.stat().st_size > 8 * 1024 * 1024
        ):
            raise UsageError(
                "--external-source requires a workspace-local SQL or JSON definition (max 8 MiB)"
            )
        raw = path.read_text()
        source_id = "external_source:local:" + digest(raw.encode())[:24]
        if path.suffix.lower() == ".json":
            # External definition packs use exactly the same asset/dependency/query/coverage contract.
            document = json.loads(raw)
            if not isinstance(document, dict) or not isinstance(
                document.get("assets"), list
            ):
                raise UsageError(
                    "external JSON must be a source definition pack with assets"
                )
            # A supplied pack may resolve an external boundary, but cannot replace observed BI definitions.
            for replacement in document["assets"]:
                existing = assets.get(replacement.get("id"))
                if existing:
                    if (
                        existing["kind"] != "external_source"
                        or existing["definition"].get("etl_definition") is not None
                    ):
                        raise UsageError(
                            "external packs may only replace unresolved external-source boundaries"
                        )
                    explicit = replacement.get("definition", {})
                    if (
                        not explicit.get("base_source")
                        and not explicit.get("etl_definition")
                        and not explicit.get("operation")
                    ):
                        raise UsageError(
                            "external replacement must identify a base source or its processing definition"
                        )
                    if replacement.get("status") != "ok":
                        raise UsageError(
                            "external boundary replacement must be explicitly resolved"
                        )
                    assets.pop(existing["id"])
                    coverage[:] = [
                        c
                        for c in coverage
                        if not (
                            c["asset_id"] == existing["id"]
                            and c["code"]
                            in {
                                "external_etl_unresolved",
                                "external_definition_required",
                                "definition_missing",
                            }
                        )
                    ]
            merge(
                {
                    key: document.get(key, [])
                    for key in ("assets", "dependencies", "queries", "coverage")
                },
                source_id,
            )
        else:
            add_asset(
                {
                    "id": source_id,
                    "kind": "external_source",
                    "name": path.name,
                    "source": {"local_path": str(path.relative_to(Path.cwd()))},
                    "definition": {"sql": raw},
                    "status": "partial",
                }
            )
            queries[source_id] = {
                "id": source_id,
                "asset_id": source_id,
                "origin": "source",
                "language": "sql",
                "text": raw,
                "status": "ok",
            }
            issue(
                source_id,
                "external_binding_required",
                "Local SQL is recorded; bind its output to a source dependency explicitly",
                status="partial",
            )

    # Resolve native published references only against actual REST IDs/content URLs, never display names.
    published = [
        a for a in assets.values() if a["kind"] == "dataset" and a["source"].get("luid")
    ]
    resolved_datasources = {}
    for asset in list(assets.values()):
        if asset["kind"] != "dataset" or asset["source"].get("luid"):
            continue
        refs = {
            asset["source"].get("published_id"),
            asset["source"].get("content_url"),
        } - {None, ""}
        if refs:
            matched = [
                a
                for a in published
                if refs & {a["source"].get("luid"), a["source"].get("content_url")}
            ]
            if len(matched) == 1:
                resolved_datasources[asset["id"]] = matched[0]["id"]
                edges.append(
                    {
                        "from": asset["id"],
                        "to": matched[0]["id"],
                        "relation": "published_datasource",
                    }
                )
            else:
                issue(
                    asset["id"],
                    "published_binding_unresolved",
                    "Published datasource reference could not be bound by stable source identity",
                    status="partial",
                )

    materialized_tables = defaultdict(set)
    for luid, scoped in datasource_lineages.items():
        if scoped is None:
            continue
        dataset_id = f"dataset:{luid}"
        verified_flows = []
        for row in scoped["flows"]:
            flow_id = f"transform:{row.get('luid')}"
            flow_asset = assets.get(flow_id)
            if (
                not row.get("id")
                or not row.get("luid")
                or not flow_asset
                or flow_asset["source"].get("metadata_id") != row["id"]
                or flow_asset.get("status") == "error"
                or flow_asset["definition"].get("operation", {}).get("type") != "flow"
            ):
                issue(
                    dataset_id,
                    "datasource_flow_identity_unresolved",
                    "Datasource upstream flow identity or native definition is unavailable",
                    status="partial",
                )
                continue
            verified_flows.append(flow_id)
            edges.append(
                {"from": dataset_id, "to": flow_id, "relation": "upstream_flow"}
            )
        # The native create-output marker distinguishes this materialized result
        # from arbitrary Hyper inputs. Never infer a producer from its table name.
        if len(scoped["flows"]) != 1 or len(verified_flows) != 1:
            continue
        flow_luid = scoped["flows"][0]["luid"]
        native_members = {
            e["to"]
            for e in edges
            if e["from"] == verified_flows[0] and e["relation"] == "definition_member"
        }
        if not any(
            member["kind"] == "transform"
            and member["source"].get("flow_luid") == flow_luid
            and member["definition"].get("operation", {}).get("type") == "output"
            for member_id in native_members
            if (member := assets.get(member_id)) is not None
        ):
            # A downloaded root with no native output cannot explain this
            # materialization. An output belonging to another flow is no proof.
            continue
        for boundary in assets.values():
            definition = boundary["definition"]
            if (
                boundary["kind"] != "external_source"
                or boundary["source"].get("luid") != luid
                or definition.get("prep_output_type") != "outputOperationTypeCreate"
            ):
                continue
            candidates = [
                a
                for a in assets.values()
                if a["kind"] == "external_source"
                and a["source"].get("luid") == luid
                and a["definition"].get("table") == definition.get("table")
            ]
            if len(candidates) != 1:
                continue
            tables = [
                row
                for row in scoped["tables"]
                if row.get("id")
                and row.get("connectionType") == "hyper"
                and row.get("fullName") == definition.get("table")
            ]
            if len(tables) != 1:
                continue
            table_id = tables[0]["id"]
            definition["etl_definition"] = {
                "metadata_datasource_id": scoped["datasource"]["id"],
                "metadata_table_id": table_id,
                "upstream_flow_luids": [scoped["flows"][0]["luid"]],
                "relationship": "upstream_flow",
            }
            edges.append(
                {
                    "from": boundary["id"],
                    "to": verified_flows[0],
                    "relation": "upstream_flow",
                }
            )
            coverage[:] = [
                c
                for c in coverage
                if not (
                    c["asset_id"] == boundary["id"]
                    and c["code"] == "external_etl_unresolved"
                )
            ]
            materialized_tables[table_id].add(dataset_id)

    # A workbook shortcut can reuse the same table across unrelated sources.
    # Resolve its shared boundary only when every observed consumer has the
    # exact datasource path just proven above; otherwise keep the original gap.
    for metadata_id, dataset_ids in materialized_tables.items():
        table_asset_id = f"external_source:table:{metadata_id}"
        consumers = {
            e["from"]
            for e in edges
            if e["to"] == table_asset_id
            and e["relation"] == "upstream_table"
            and assets.get(e["from"], {}).get("kind") == "chart"
        }
        proof_edges = [
            e
            for e in edges
            if e["relation"] in {"depends_on", "published_datasource", "upstream_flow"}
        ]
        if not consumers or not all(
            _reachable({consumer}, proof_edges) & dataset_ids for consumer in consumers
        ):
            continue
        table_asset = assets.get(table_asset_id)
        if table_asset:
            table_asset["definition"]["etl_definition"] = {
                "datasource_ids": sorted(dataset_ids),
                "relationship": "upstream_flow",
            }
            table_asset["status"] = "ok"
            for dataset_id in dataset_ids:
                edges.append(
                    {
                        "from": table_asset_id,
                        "to": dataset_id,
                        "relation": "materialized_from",
                    }
                )
            coverage[:] = [
                c
                for c in coverage
                if not (
                    c["asset_id"] == table_asset_id
                    and c["code"] == "external_definition_required"
                )
            ]

    for workbook_id, chart_ids, local_ids, upstream_id in datasource_bindings:
        if any(
            resolved_datasources.get(local_id) == upstream_id for local_id in local_ids
        ):
            continue
        for asset_id in [workbook_id, *chart_ids]:
            issue(
                asset_id,
                "lineage_binding_unresolved",
                "Upstream datasources is known at workbook level but its selected chart/dataset binding needs explicit resolution",
                status="partial",
            )
        for chart_id in chart_ids:
            edges.append(
                {
                    "from": chart_id,
                    "to": upstream_id,
                    "relation": "unresolved_upstream_datasources",
                }
            )

    for workbook_id, chart_ids, flow_id in flow_bindings:
        proof_edges = [
            e
            for e in edges
            if e["relation"] in {"depends_on", "published_datasource", "upstream_flow"}
        ]
        unresolved = [
            chart_id
            for chart_id in chart_ids
            if flow_id not in _reachable({chart_id}, proof_edges)
        ]
        if not unresolved:
            continue
        for asset_id in [workbook_id, *unresolved]:
            issue(
                asset_id,
                "lineage_binding_unresolved",
                "Upstream flows is known at workbook level but its selected chart/dataset binding needs explicit resolution",
                status="partial",
            )
        for chart_id in unresolved:
            edges.append(
                {
                    "from": chart_id,
                    "to": flow_id,
                    "relation": "unresolved_upstream_flows",
                }
            )

    for edge in edges:
        for identifier in (edge["from"], edge["to"]):
            if identifier not in assets:
                add_asset(
                    {
                        "id": identifier,
                        "kind": "external_source",
                        "name": identifier,
                        "source": {},
                        "definition": {},
                        "status": "partial",
                    }
                )
                issue(
                    identifier,
                    "definition_missing",
                    "Dependency definition was not available",
                    status="partial",
                )
    edges = list({(e["from"], e["to"], e["relation"]): e for e in edges}.values())
    for identifier in _cycles(list(assets.values()), edges):
        issue(
            identifier,
            "dependency_cycle",
            "Source dependency graph contains a cycle",
            status="unsupported",
        )
    from .transforms import generate_transforms

    from .bindings import apply_source_bindings, required_bindings

    derived_assets = apply_source_bindings(
        list(assets.values()), source_bindings, dialect=dialect
    )
    generated = generate_transforms(derived_assets, edges, dialect=dialect)
    generated["pipeline"]["source_bindings_required"] = required_bindings(
        derived_assets
    )
    for node in generated.get("pipeline", {}).get("nodes", []):
        if node.get("status") == "blocked":
            issue(
                node["id"],
                "processing_model_blocked",
                node.get("error") or "Processing model could not be generated",
                status="unsupported",
            )
    for name, sql in generated.get("models", {}).items():
        extra[name] = sql.encode()
    for name, transform in generated.get("transforms", {}).items():
        extra[name] = json_bytes(transform)
    extra["pipeline.json"] = json_bytes(generated.get("pipeline", {}))
    extra["validation.json"] = json_bytes(generated.get("validation", {}))
    for query in queries.values():
        if query["language"] == "sql":
            filename = f"sql/{digest(query['id'].encode())[:24]}.sql"
            query["file"] = filename
            extra[filename] = (query["text"].rstrip() + "\n").encode()
    documents = {
        "assets": sorted(assets.values(), key=lambda a: a["id"]),
        "dependencies": sorted(
            edges, key=lambda e: (e["from"], e["to"], e["relation"])
        ),
        "queries": sorted(queries.values(), key=lambda q: q["id"]),
        "coverage": coverage,
    }
    partial = any(c["status"] != "ok" for c in coverage) or any(
        a["status"] != "ok" for a in assets.values()
    )
    manifest = {
        "schema_version": 1,
        "platform": "tableau",
        "source": source,
        "selection": selection,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial" if partial else "ok",
        "summary": {
            "dashboards": len(chosen),
            "assets": len(assets),
            "queries": len(queries),
            "failures": failures,
            "coverage_issues": len(coverage),
        },
    }
    stored = write_bundle(target, manifest, documents, extra, overwrite=overwrite)
    return {"output_dir": str(target), **stored}
