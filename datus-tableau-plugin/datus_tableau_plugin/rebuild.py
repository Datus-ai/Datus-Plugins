"""Offline handoff to Datus' existing metric and dashboard tools.

This module neither executes SQL nor creates metric recipes. Its report is a
preflight plan; the real ``save_metric_query_template`` tool still owns metric
compilation, warehouse policy enforcement, trial data and model snapshots.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .errors import PluginError

_REQUEST_KEYS = {
    "metric",
    "dimensions",
    "time_start",
    "time_end",
    "time_granularity",
    "filters",
    "order_by",
    "limit",
    "metric_params",
}
_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "is_not_null"}
_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
_DIMENSION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _identity(value: Any) -> tuple[tuple[str, ...], str]:
    if not isinstance(value, dict) or set(value) != {"path", "name"}:
        raise ValueError("metric identity must contain exactly path and name")
    path, name = value["path"], value["name"]
    if (
        not isinstance(path, list)
        or not path
        or any(not isinstance(p, str) or not p.strip() for p in path)
    ):
        raise ValueError(
            "metric path must be the nonempty segment list returned by discovery"
        )
    if not isinstance(name, str) or not name.strip():
        raise ValueError("metric name must be nonempty")
    return tuple(path), name


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _parameter_names(query: dict[str, Any]) -> set[str]:
    values = [query.get("time_start"), query.get("time_end")]
    values += [item.get("value") for item in query.get("filters", [])]
    values += list(query.get("metric_params", {}).values())
    names = set()
    for value in values:
        if isinstance(value, dict):
            if set(value) != {"param"} or not isinstance(value["param"], str):
                raise ValueError(
                    "value bindings must be {param: name}; identifiers cannot be bound"
                )
            names.add(value["param"])
    return names


def _query_call(
    item: dict[str, Any], catalog: dict[Any, list[dict[str, Any]]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(item, dict):
        raise ValueError("each query binding must be an object")
    query = item.get("query")
    if not isinstance(query, dict) or set(query) - _REQUEST_KEYS:
        raise ValueError(
            "query must use MetricQueryRequest fields, without SQL or fallback fields"
        )
    identity = _identity(query.get("metric"))
    candidates = catalog.get(identity, [])
    if len(candidates) != 1:
        raise ValueError(
            "metric identity is missing or ambiguous in the discovered DOSI catalogue"
        )
    metric = candidates[0]
    datasource = metric.get("datasource")
    revision = metric.get("model_revision", "")
    if (
        not isinstance(datasource, str)
        or not datasource
        or not isinstance(revision, str)
        or not _SHA256.fullmatch(revision)
    ):
        raise ValueError(
            "DOSI catalogue requires its real datasource and SHA256 model_revision"
        )
    if item.get("datasource") != datasource or item.get("model_revision") != revision:
        raise ValueError(
            "selected datasource/model revision differs from discovered DOSI binding"
        )
    dimensions = query.get("dimensions", [])
    supported = metric.get("dimensions")
    if (
        not isinstance(supported, list)
        or any(not isinstance(d, str) for d in supported)
        or not isinstance(dimensions, list)
        or len(dimensions) > 30
        or len(set(dimensions)) != len(dimensions)
        or any(
            not isinstance(d, str) or not _DIMENSION.fullmatch(d) or d not in supported
            for d in dimensions
        )
    ):
        raise ValueError(
            "query dimensions must be unique dimensions supported by the discovered metric"
        )
    if not set(metric.get("required_dimensions", [])).issubset(dimensions):
        raise ValueError("query omits required metric dimensions")
    grain = query.get("time_granularity")
    if grain is not None and grain not in metric.get("time_granularities", []):
        raise ValueError("time grain is not supported by the discovered metric")
    filters = query.get("filters", [])
    if not isinstance(filters, list) or len(filters) > 30:
        raise ValueError("filters must be a list with at most 30 entries")
    for item_filter in filters:
        if (
            not isinstance(item_filter, dict)
            or set(item_filter) - {"dimension", "op", "value"}
            or item_filter.get("dimension") not in supported
            or item_filter.get("op", "eq") not in _OPS
        ):
            raise ValueError(
                "filters require a discovered dimension and a supported structured operator"
            )
    order_by = query.get("order_by", [])
    order_keys = {*dimensions, identity[1]}
    if grain is not None and "metric_time" in dimensions:
        order_keys.add(f"metric_time__{grain}")
    if (
        not isinstance(order_by, list)
        or len(order_by) > 30
        or any(
            not isinstance(key, str) or key.lstrip("-") not in order_keys
            for key in order_by
        )
    ):
        raise ValueError(
            "order_by must reference metric or projected dimension output names"
        )
    limit = query.get("limit", 10000)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100000
    ):
        raise ValueError("metric row limit must be an integer between 1 and 100000")
    metric_params = query.get("metric_params", {})
    if not isinstance(metric_params, dict) or set(metric_params) - set(
        metric.get("parameters", [])
    ):
        raise ValueError(
            "metric_params contains a parameter not declared by the discovered metric"
        )
    params = item.get("params", [])
    samples = item.get("sample_params", {})
    if not isinstance(params, list) or not isinstance(samples, dict):
        raise ValueError("params and sample_params must be a list and object")
    declarations = [p.get("name") for p in params if isinstance(p, dict)]
    if len(declarations) != len(params) or any(
        not isinstance(p, str) for p in declarations
    ):
        raise ValueError("each parameter declaration requires a name")
    if len(set(declarations)) != len(declarations) or set(
        declarations
    ) != _parameter_names(query):
        raise ValueError(
            "parameter declarations must exactly match metric value bindings"
        )
    if set(samples) - set(declarations):
        raise ValueError("sample_params contains undeclared parameters")
    if any(
        p.get("required") and (p["name"] not in samples or samples[p["name"]] is None)
        for p in params
    ):
        raise ValueError("required parameter has no trial value")
    name = item.get("name", "")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(
            "query name must use 1-64 lowercase letters, digits or underscores"
        )
    if any(
        not isinstance(item.get(key), str) or not item[key].strip()
        for key in ("goal", "hypothesis")
    ):
        raise ValueError("goal and hypothesis are required")
    if not isinstance(item.get("caveats", ""), str):
        raise ValueError("caveats must be text")
    # Serialize now to reject NaN and objects that cannot be handed to tools.
    _canonical(query)
    call = {key: item[key] for key in ("name", "query", "goal", "hypothesis")}
    call.update(params=params, sample_params=samples, caveats=item.get("caveats", ""))
    return call, {
        "metric": query["metric"],
        "datasource": datasource,
        "model_revision": revision,
    }


def assess_rebuild(
    bundle: dict[str, Any],
    *,
    bindings: dict[str, Any],
    metric_catalog: list[dict[str, Any]],
    source_manifest_sha256: str,
) -> dict[str, Any]:
    """Evaluate explicit bindings; do not guess metrics or manufacture artifacts.

    ``bundle`` is the result of ``bundle.inspect_bundle``. Catalogue entries are
    normalized projections of DOSI discovery, not metric definitions to author.
    Only ``plan_rebuild`` should load untrusted on-disk bundles.
    """
    if (
        not isinstance(bindings, dict)
        or bindings.get("schema_version") != 1
        or bindings.get("source_manifest_sha256") != source_manifest_sha256
    ):
        raise ValueError(
            "bindings must pin schema_version 1 and the exact source manifest SHA256"
        )
    if not isinstance(metric_catalog, list) or any(
        not isinstance(metric, dict) for metric in metric_catalog
    ):
        raise ValueError("metric catalogue must be a list of discovered metric records")
    assets = {asset["id"]: asset for asset in bundle["assets"]}
    if len(assets) != len(bundle["assets"]):
        raise ValueError("duplicate source asset identity")
    charts = {key: item for key, item in assets.items() if item["kind"] == "chart"}
    catalog: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for item in metric_catalog:
        catalog[_identity(item.get("metric"))].append(item)
    selected = bindings.get("charts")
    if not isinstance(selected, list):
        raise ValueError("bindings.charts must be a list")
    by_chart = {}
    for item in selected:
        if not isinstance(item, dict) or item.get("chart_id") not in charts:
            raise ValueError(
                "binding chart_id does not identify a chart in the verified bundle"
            )
        if item["chart_id"] in by_chart:
            raise ValueError("duplicate chart binding")
        by_chart[item["chart_id"]] = item
    dependencies: dict[str, list[str]] = defaultdict(list)
    for edge in bundle["dependencies"]:
        dependencies[edge["from"]].append(edge["to"])
    coverage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in bundle["coverage"]:
        if issue.get("status") not in {"ok", "complete", "supported"}:
            coverage[issue.get("asset_id", "")].append(issue)

    def blockers(asset_id: str, resolutions: set[tuple[str, str]]) -> list[str]:
        reasons = []
        active, visited = set(), set()
        pending = [(asset_id, 0, False)]
        while pending:
            current, depth, finished = pending.pop()
            if finished:
                active.remove(current)
                visited.add(current)
                continue
            if current in active:
                reasons.append(f"dependency cycle: {current}")
                continue
            if current in visited:
                continue
            if depth > 64 or len(visited) + len(active) >= 4096:
                reasons.append(f"dependency traversal resource limit: {current}")
                continue
            asset = assets.get(current)
            if asset is None:
                reasons.append(f"missing dependency: {current}")
                visited.add(current)
                continue
            active.add(current)
            pending.append((current, depth, True))
            issues = coverage.get(current, [])
            historical_only = (
                asset["kind"] == "external_source"
                and bool(issues)
                and all((current, issue.get("code")) in resolutions for issue in issues)
            )
            if asset.get("status") != "ok" and not (
                asset.get("status") == "partial" and historical_only
            ):
                reasons.append(
                    f"source asset {current} is {asset.get('status', 'unknown')}"
                )
            reasons += [
                f"{current}: {issue.get('code', 'coverage_gap')}"
                for issue in issues
                if (current, issue.get("code")) not in resolutions
                and not (asset["kind"] == "dataset" and issue.get("field_id"))
            ]
            pending.extend(
                (dependency, depth + 1, False)
                for dependency in reversed(dependencies[current])
            )
        return reasons

    results = []
    used_names: set[str] = set()
    for chart_id, chart in charts.items():
        reasons = []
        reasons += [
            issue.get("code", "global_coverage_gap") for issue in coverage.get("", [])
        ]
        item = by_chart.get(chart_id)
        calls = []
        identities = []
        differences = []
        resolutions: set[tuple[str, str]] = set()
        resolution_notes = []
        if item is None:
            reasons.append("no explicit DOSI binding")
        else:
            differences = item.get("differences", [])
            if not isinstance(differences, list) or any(
                not isinstance(d, str) or not d.strip() for d in differences
            ):
                reasons.append(
                    "differences must be a list of explicit semantic differences"
                )
                differences = []
            queries = item.get("queries", [])
            if not isinstance(queries, list) or not queries:
                reasons.append("at least one discovered metric query is required")
                queries = []
            for query in queries:
                try:
                    call, identity = _query_call(query, catalog)
                    if call["name"] in used_names:
                        raise ValueError(
                            "query names must be unique across the target dashboard"
                        )
                    used_names.add(call["name"])
                    calls.append(call)
                    identities.append(identity)
                except (TypeError, ValueError, KeyError) as exc:
                    reasons.append(str(exc))
            if len(calls) > 1:
                if item.get("combination") != "independent":
                    reasons.append(
                        "multi-metric alignment requires runtime grain/key validation; use independent queries only"
                    )
            requested_resolutions = item.get("source_gap_resolutions", [])
            if not isinstance(requested_resolutions, list):
                reasons.append("source_gap_resolutions must be a list")
                requested_resolutions = []
            for resolution in requested_resolutions:
                if not isinstance(resolution, dict):
                    reasons.append("source gap resolution must be an object")
                    continue
                source_id, code = resolution.get("asset_id"), resolution.get("code")
                if (
                    not isinstance(source_id, str)
                    or assets.get(source_id, {}).get("kind") != "external_source"
                    or code
                    not in {"external_etl_unresolved", "external_definition_required"}
                    or not any(
                        issue.get("code") == code
                        for issue in coverage.get(source_id, [])
                    )
                    or resolution.get("resolution") != "existing_dosi_target"
                    or not isinstance(resolution.get("evidence"), str)
                    or not resolution["evidence"].strip()
                    or not identities
                    or sorted(map(_canonical, resolution.get("metric_bindings", [])))
                    != sorted(map(_canonical, identities))
                ):
                    reasons.append(
                        "only an exact historical ETL gap tied to the discovered target metric bindings can be resolved"
                    )
                    continue
                resolutions.add((source_id, code))
                resolution_notes.append(
                    {
                        **resolution,
                        "source_status": "historical_definition_not_synchronized",
                        "target_validation": "pending_metric_engine_trial",
                    }
                )
        reasons += blockers(chart_id, resolutions)
        reasons = sorted(set(reasons))
        results.append(
            {
                "chart_id": chart_id,
                "name": chart["name"],
                "status": "blocked" if reasons else "ready_for_metric_validation",
                "reasons": reasons,
                "differences": differences,
                "metric_bindings": identities,
                "source_gap_resolutions": resolution_notes,
                "tool_calls": []
                if reasons
                else [
                    {"tool": "save_metric_query_template", "arguments": call}
                    for call in calls
                ],
            }
        )
    ready = sum(result["status"] == "ready_for_metric_validation" for result in results)
    return {
        "schema_version": 1,
        "kind": "dosi_dashboard_rebuild_plan",
        "source_manifest_sha256": source_manifest_sha256,
        "source_status": bundle.get("manifest", {}).get("status", "not_supplied"),
        "status": "ready_for_metric_validation"
        if ready and ready == len(results)
        else "blocked"
        if not ready
        else "partial",
        "charts": results,
        "summary": {
            "total": len(results),
            "ready": ready,
            "blocked": len(results) - ready,
        },
        "validation": {
            "metric_engine_executed": False,
            "dashboard_rendered": False,
            "published": False,
        },
    }


def plan_rebuild(
    bundle_path: str | Path,
    *,
    bindings: dict[str, Any],
    metric_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    from .bundle import inspect_bundle

    path = Path(bundle_path)
    bundle = inspect_bundle(path)
    return assess_rebuild(
        bundle,
        bindings=bindings,
        metric_catalog=metric_catalog,
        # Use the exact bytes verified under the snapshot read lock, including a
        # read-only recovery backup when a previous writer died mid-exchange.
        source_manifest_sha256=bundle["manifest_sha256"],
    )


def _workspace_json(filename: str) -> Any:
    from .bundle import workspace_path

    path = workspace_path(filename)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("rebuild input JSON exceeds 8 MiB")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a verified source bundle and explicit DOSI bindings; prints JSON only"
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--bindings", required=True)
    parser.add_argument("--metric-catalog", required=True)
    args = parser.parse_args(argv)
    try:
        result = plan_rebuild(
            args.bundle,
            bindings=_workspace_json(args.bindings),
            metric_catalog=_workspace_json(args.metric_catalog),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0 if result["status"] == "ready_for_metric_validation" else 1
    except (PluginError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
