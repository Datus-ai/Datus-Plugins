"""Bounded presentation of an already verified source snapshot; no file I/O."""

from __future__ import annotations

from collections import Counter
import json

MAX_SUMMARY_BYTES = 4096
MAX_SUMMARY_ITEMS = 10
MAX_SUMMARY_TEXT = 64
MAX_PROCESSING_DOCUMENT_BYTES = 8 * 1024 * 1024


def summarize_bundle(
    bundle: dict, processing: dict[str, tuple[bytes | None, int]]
) -> dict:
    """Summarize only data captured under the source reader's verification lock."""
    manifest = bundle["manifest"]
    omitted = {
        key: 0
        for key in (
            "charts",
            "coverage_codes",
            "dashboard_ids",
            "project_ids",
            "source_fields",
            "selection_fields",
            "processing_documents",
        )
    }
    truncation = {"text_values": 0, "unsupported_values": 0}

    def text(value):
        if value is None:
            return None
        if not isinstance(value, str):
            truncation["unsupported_values"] += 1
            return None
        if len(value) > MAX_SUMMARY_TEXT:
            truncation["text_values"] += 1
            return value[: MAX_SUMMARY_TEXT - 1] + "…"
        return value

    def count(value):
        if type(value) is int and 0 <= value <= 2**63 - 1:
            return value
        if value is not None:
            truncation["unsupported_values"] += 1
        return None

    def identifiers(values, field):
        if not isinstance(values, list):
            truncation["unsupported_values"] += 1
            return []
        omitted[field] = max(0, len(values) - MAX_SUMMARY_ITEMS)
        return [text(value) for value in values[:MAX_SUMMARY_ITEMS]]

    source_keys = {"server_url", "site_content_url"}
    selection_keys = {"dashboard_ids", "project_ids", "all"}
    source = manifest["source"]
    selection = manifest["selection"]
    omitted["source_fields"] = len(set(source) - source_keys)
    omitted["selection_fields"] = len(set(selection) - selection_keys)
    if (
        "all" in selection
        and selection["all"] is not None
        and not isinstance(selection["all"], bool)
    ):
        truncation["unsupported_values"] += 1
    kinds = Counter(asset["kind"] for asset in bundle["assets"])
    charts = [asset for asset in bundle["assets"] if asset["kind"] == "chart"]
    codes = Counter(issue["code"] for issue in bundle["coverage"])
    omitted["charts"] = max(0, len(charts) - MAX_SUMMARY_ITEMS)
    omitted["coverage_codes"] = max(0, len(codes) - MAX_SUMMARY_ITEMS)
    result = {
        "schema_version": 1,
        "summary_format": "tableau-source-summary-v1",
        "status": manifest["status"],
        "source": {
            key: text(source[key]) for key in sorted(source_keys & source.keys())
        },
        "selection": {
            "dashboard_ids": identifiers(
                selection.get("dashboard_ids", []), "dashboard_ids"
            ),
            "project_ids": identifiers(selection.get("project_ids", []), "project_ids"),
            "all": selection.get("all")
            if isinstance(selection.get("all"), bool)
            else None,
        },
        "summary": {
            "assets": len(bundle["assets"]),
            "dashboards": kinds["dashboard"],
            "charts": kinds["chart"],
            "queries": len(bundle["queries"]),
            "dependencies": len(bundle["dependencies"]),
            "coverage_issues": len(bundle["coverage"]),
        },
        "manifest_sha256": bundle["manifest_sha256"],
        "resolved_path": text(bundle["resolved_path"]),
        "recovered_from_backup": False,
        "asset_kinds": dict(sorted(kinds.items())),
        "charts": [
            {"id": text(asset["id"]), "name": text(asset["name"])}
            for asset in charts[:MAX_SUMMARY_ITEMS]
        ],
        "coverage": {
            "status_counts": dict(
                sorted(Counter(issue["status"] for issue in bundle["coverage"]).items())
            ),
            "codes": [
                {"code": text(code), "count": total}
                for code, total in sorted(codes.items())[:MAX_SUMMARY_ITEMS]
            ],
        },
        "processing": {"pipeline": None, "validation": None},
        "omitted_counts": omitted,
        "truncation": truncation,
        "limits": {
            "json_bytes": MAX_SUMMARY_BYTES,
            "items_per_list": MAX_SUMMARY_ITEMS,
            "text_chars": MAX_SUMMARY_TEXT,
            "processing_document_bytes": MAX_PROCESSING_DOCUMENT_BYTES,
        },
    }
    for filename, (content, size) in processing.items():
        key = filename.removesuffix(".json")
        if content is None:
            omitted["processing_documents"] += 1
            result["processing"][key] = {"summary_omitted": True, "bytes": size}
            continue
        document = json.loads(content)
        if not isinstance(document, dict):
            raise ValueError("processing summary requires JSON objects")
        if key == "pipeline":
            nodes = document.get("nodes", [])
            if not isinstance(nodes, list) or any(
                not isinstance(node, dict) for node in nodes
            ):
                raise ValueError("pipeline summary requires node objects")
            statuses = Counter(
                node.get("status")
                if node.get("status") in {"generated", "blocked", "partial", "error"}
                else "unknown"
                for node in nodes
            )
            result["processing"][key] = {
                "status": text(document.get("status")),
                "execution": text(document.get("execution")),
                "dialect": text(document.get("dialect")),
                "nodes": len(nodes),
                "node_status_counts": dict(sorted(statuses.items())),
            }
        else:
            result["processing"][key] = {
                "generated": count(document.get("generated")),
                "blocked": count(document.get("blocked")),
                "execution": text(document.get("execution")),
            }
    # Size is measured using the exact pretty-JSON rendering of the CLI. Long
    # Unicode labels can require a smaller list than the item-count limit.
    collections = [
        (result, "charts", "charts"),
        (result["coverage"], "codes", "coverage_codes"),
        (result["selection"], "dashboard_ids", "dashboard_ids"),
        (result["selection"], "project_ids", "project_ids"),
    ]
    while True:
        result["truncated"] = any(omitted.values()) or any(truncation.values())
        if (
            len(json.dumps(result, ensure_ascii=False, indent=2).encode()) + 1
            <= MAX_SUMMARY_BYTES
        ):
            return result
        for owner, field, omitted_key in collections:
            if owner[field]:
                owner[field].pop()
                omitted[omitted_key] += 1
                break
        else:
            raise ValueError("source summary exceeds its fixed byte limit")
