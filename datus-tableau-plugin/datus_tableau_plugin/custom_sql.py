"""Qualify a narrow Catalog lineage gap against independently retrieved native SQL."""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def native_sql_evidence(bundle: dict, chart_id: str, query_id: str) -> dict | None:
    """Return exact native PostgreSQL evidence, never infer missing source semantics.

    Tableau's unsupported-Custom-SQL flag concerns Catalog lineage. Only a complete
    matching native relation on one proven physical connection can cover that
    specific gap. Other gaps, SQL differences and uncertain connections stay blocked.
    """
    assets = {asset["id"]: asset for asset in bundle["assets"]}
    query_asset = assets.get(query_id, {})
    if (
        query_asset.get("kind") != "query"
        or query_asset.get("status") != "partial"
        or query_asset.get("definition", {}).get("is_unsupported_custom_sql")
        is not True
    ):
        return None
    issues = [
        issue for issue in bundle["coverage"] if issue.get("asset_id") == query_id
    ]
    if not issues or any(
        issue.get("code") != "custom_sql_lineage_incomplete"
        or issue.get("status") != "partial"
        for issue in issues
    ):
        return None
    queries = bundle["queries"]
    records = [query for query in queries if query.get("asset_id") == query_id]
    if len(records) != 1:
        return None
    metadata_query = records[0]
    text = metadata_query.get("text")
    if (
        metadata_query.get("origin") != "source"
        or metadata_query.get("status") != "partial"
        or metadata_query.get("language") != "sql"
        or not isinstance(text, str)
        or not text.strip()
    ):
        return None
    database = query_asset.get("source", {}).get("database", {})
    if (
        not isinstance(database, dict)
        or database.get("__typename") != "DatabaseServer"
        or database.get("connectionType") != "postgres"
    ):
        return None
    host, name = database.get("hostName"), database.get("name")
    port = str(database.get("port") or "5432")
    if (
        not isinstance(host, str)
        or not host
        or not isinstance(name, str)
        or not name
        or not port.isdigit()
        or not 0 < int(port) <= 65535
    ):
        return None
    try:
        statements = sqlglot.parse(text, read="postgres")
    except sqlglot.errors.SqlglotError:
        return None
    if (
        len(statements) != 1
        or not isinstance(statements[0], exp.Query)
        or statements[0].find(exp.Into, exp.Command)
    ):
        return None
    dependencies = {
        edge["to"] for edge in bundle["dependencies"] if edge["from"] == chart_id
    }
    if query_id not in dependencies:
        return None
    workbook_id = assets.get(chart_id, {}).get("source", {}).get("workbook_luid")
    if not workbook_id or not any(
        edge["from"] == f"workbook:{workbook_id}" and edge["to"] == query_id
        for edge in bundle["dependencies"]
    ):
        return None
    matches = []
    for dataset_id in dependencies:
        dataset = assets.get(dataset_id, {})
        if (
            dataset.get("kind") != "dataset"
            or dataset.get("status") != "ok"
            or dataset.get("source", {}).get("workbook_luid") != workbook_id
        ):
            continue
        connections = dataset.get("definition", {}).get("connections", [])
        if not isinstance(connections, list) or any(
            not isinstance(connection, dict) for connection in connections
        ):
            continue
        physical = [
            connection
            for connection in connections
            if connection.get("class") != "federated"
        ]
        if len(physical) != 1:
            continue
        connection = physical[0]
        if (
            connection.get("class") != "postgres"
            or str(connection.get("server", "")).lower() != host.lower()
            or connection.get("dbname") != name
            or str(connection.get("port") or "5432") != port
        ):
            continue
        native = [query for query in queries if query.get("asset_id") == dataset_id]
        # Several native references are fine only when every one has the same
        # complete SQL. A different relation cannot be silently left out.
        if native and all(
            query.get("origin") == "source"
            and query.get("language") == "sql"
            and query.get("status") == "ok"
            and isinstance(query.get("text"), str)
            and query["text"].strip() == text.strip()
            for query in native
        ):
            matches.append(
                {
                    "asset_id": query_id,
                    "dataset_id": dataset_id,
                    "metadata_query_id": metadata_query["id"],
                    "native_query_ids": [query["id"] for query in native],
                    "physical_identity": {
                        "type": "postgres",
                        "host": host,
                        "port": port,
                        "database": name,
                    },
                    "source_status": "catalog_lineage_incomplete",
                    "target_validation": "pending_metric_engine_trial",
                }
            )
    return matches[0] if len(matches) == 1 else None
