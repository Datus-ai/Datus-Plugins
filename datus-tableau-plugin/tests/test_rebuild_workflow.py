from __future__ import annotations

import copy
import hashlib
import json

import pytest

from datus_tableau_plugin.bundle import write_bundle
from datus_tableau_plugin.errors import PluginError
from datus_tableau_plugin.rebuild import assess_rebuild, main, plan_rebuild


SOURCE_HASH = "a" * 64
MODEL_HASH = "b" * 64
CHART = "chart:workbook-1:Revenue"


def source():
    return {
        "assets": [
            {
                "id": CHART,
                "kind": "chart",
                "name": "Revenue",
                "source": {"workbook_luid": "workbook-1"},
                "definition": {},
                "status": "ok",
            },
            {
                "id": "dataset:shared",
                "kind": "dataset",
                "name": "Sales",
                "source": {},
                "definition": {},
                "status": "ok",
            },
        ],
        "dependencies": [{"from": CHART, "to": "dataset:shared", "relation": "reads"}],
        "queries": [
            {
                "id": "query:old",
                "asset_id": "dataset:shared",
                "origin": "source",
                "language": "sql",
                "text": "SELECT sum(gross_sales) FROM original_sales",
                "status": "ok",
            }
        ],
        "coverage": [],
    }


def catalog():
    return [
        {
            "metric": {"path": ["Commerce", "Revenue"], "name": "net_revenue"},
            "datasource": "warehouse",
            "model_revision": MODEL_HASH,
            "dimensions": ["region", "metric_time"],
            "required_dimensions": [],
            "time_granularities": ["day", "month"],
            "parameters": [],
        }
    ]


def binding():
    return {
        "schema_version": 1,
        "source_manifest_sha256": SOURCE_HASH,
        "charts": [
            {
                "chart_id": CHART,
                "differences": ["Current metric subtracts refunds."],
                "queries": [
                    {
                        "name": "revenue_by_region",
                        "datasource": "warehouse",
                        "model_revision": MODEL_HASH,
                        "goal": "Compare current net revenue by region",
                        "hypothesis": "Regions explain net revenue composition",
                        "query": {
                            "metric": {
                                "path": ["Commerce", "Revenue"],
                                "name": "net_revenue",
                            },
                            "dimensions": ["region"],
                            "time_start": {"param": "start_date"},
                            "filters": [
                                {
                                    "dimension": "region",
                                    "op": "eq",
                                    "value": {"param": "region"},
                                }
                            ],
                        },
                        "params": [
                            {"name": "start_date", "type": "date", "required": True},
                            {"name": "region", "type": "string", "required": False},
                        ],
                        "sample_params": {"start_date": "2026-01-01", "region": "East"},
                    }
                ],
            }
        ],
    }


def assess(doc=None, bound=None, metrics=None):
    return assess_rebuild(
        source() if doc is None else doc,
        bindings=binding() if bound is None else bound,
        metric_catalog=catalog() if metrics is None else metrics,
        source_manifest_sha256=SOURCE_HASH,
    )


def test_existing_dosi_identity_is_handed_to_actual_tool_without_source_sql():
    report = assess()
    assert report["summary"] == {"total": 1, "ready": 1, "blocked": 0}
    assert report["status"] == "ready_for_metric_validation"
    chart = report["charts"][0]
    assert chart["differences"] == ["Current metric subtracts refunds."]
    assert chart["metric_bindings"] == [
        {
            "metric": catalog()[0]["metric"],
            "datasource": "warehouse",
            "model_revision": MODEL_HASH,
        }
    ]
    call = chart["tool_calls"][0]
    assert call["tool"] == "save_metric_query_template"
    assert call["arguments"]["query"] == binding()["charts"][0]["queries"][0]["query"]
    assert call["arguments"]["sample_params"] == {
        "start_date": "2026-01-01",
        "region": "East",
    }
    assert "original_sales" not in json.dumps(report)
    assert report["validation"] == {
        "metric_engine_executed": False,
        "dashboard_rendered": False,
        "published": False,
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc["charts"][0]["queries"][0]["query"]["metric"].update(
            path=["Another", "Revenue"]
        ),
        lambda doc: doc["charts"][0]["queries"][0]["query"].update(
            dimensions=["unknown"]
        ),
        lambda doc: doc["charts"][0]["queries"][0]["query"].update(
            time_granularity="week"
        ),
        lambda doc: doc["charts"][0]["queries"][0].update(datasource="other_warehouse"),
        lambda doc: doc["charts"][0]["queries"][0].update(model_revision="c" * 64),
        lambda doc: doc["charts"][0]["queries"][0]["query"].update(
            sql="SELECT * FROM original_sales"
        ),
        lambda doc: doc["charts"][0]["queries"][0]["query"].update(
            metric_params={"undeclared": 1}
        ),
        lambda doc: doc["charts"][0]["queries"][0]["query"]["filters"][0].update(
            op="raw_sql"
        ),
        lambda doc: doc["charts"][0]["queries"][0].update(params=[]),
        lambda doc: doc["charts"][0]["queries"][0].update(
            sample_params={"region": "East"}
        ),
        lambda doc: doc["charts"][0]["queries"][0]["query"].update(limit=True),
    ],
)
def test_invalid_or_missing_metric_semantics_block_without_sql_fallback(change):
    bound = binding()
    change(bound)
    report = assess(bound=bound)
    assert report["status"] == "blocked"
    assert report["charts"][0]["reasons"]
    assert report["charts"][0]["tool_calls"] == []


def test_duplicate_discovery_identity_is_ambiguous():
    report = assess(metrics=catalog() * 2)
    assert report["status"] == "blocked"
    assert "ambiguous" in report["charts"][0]["reasons"][0]


def test_unused_complex_field_does_not_block_other_consumers_of_a_dataset():
    doc = source()
    doc["coverage"] = [
        {
            "asset_id": "dataset:shared",
            "field_id": "unused_running_total",
            "status": "partial",
            "code": "tableau_calculation",
            "message": "Unused calculation retained",
        }
    ]
    assert assess(doc)["summary"]["ready"] == 1
    doc["coverage"].append(
        {
            "asset_id": CHART,
            "status": "unsupported",
            "code": "tableau_calculation",
            "message": "Chart uses this calculation",
        }
    )
    assert assess(doc)["summary"]["blocked"] == 1


def test_time_order_key_uses_the_metric_engines_projected_grain_name():
    bound = binding()
    query = bound["charts"][0]["queries"][0]["query"]
    query.update(
        dimensions=["metric_time"],
        time_granularity="month",
        order_by=["-metric_time__month"],
    )
    assert assess(bound=bound)["summary"]["ready"] == 1


def test_source_checksum_and_asset_identity_cannot_be_reused_after_change():
    bound = binding()
    bound["source_manifest_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="manifest SHA256"):
        assess(bound=bound)
    bound = binding()
    bound["charts"][0]["chart_id"] = "chart:another-workbook:Revenue"
    with pytest.raises(ValueError, match="chart_id"):
        assess(bound=bound)


def test_nested_unsupported_etl_blocks_only_its_dependents():
    doc = source()
    doc["assets"] += [
        {
            "id": "transform:unknown",
            "kind": "transform",
            "name": "External script",
            "source": {},
            "definition": {},
            "status": "unsupported",
        },
        {
            "id": "dataset:intermediate",
            "kind": "dataset",
            "name": "Prepared",
            "source": {},
            "definition": {},
            "status": "ok",
        },
        {
            "id": "chart:independent",
            "kind": "chart",
            "name": "Independent",
            "source": {},
            "definition": {},
            "status": "ok",
        },
    ]
    doc["dependencies"] += [
        {"from": "dataset:shared", "to": "dataset:intermediate", "relation": "reads"},
        {
            "from": "dataset:intermediate",
            "to": "transform:unknown",
            "relation": "produced_by",
        },
    ]
    doc["coverage"] = [
        {
            "asset_id": "transform:unknown",
            "status": "unsupported",
            "code": "custom_script",
            "message": "No converter",
        }
    ]
    bound = binding()
    independent = copy.deepcopy(bound["charts"][0])
    independent["chart_id"] = "chart:independent"
    independent["queries"][0]["name"] = "independent_revenue"
    bound["charts"].append(independent)
    report = assess(doc, bound)
    assert report["status"] == "partial"
    assert report["summary"] == {"total": 2, "ready": 1, "blocked": 1}
    assert report["charts"][0]["tool_calls"] == []
    assert "transform:unknown: custom_script" in report["charts"][0]["reasons"]
    assert report["charts"][1]["status"] == "ready_for_metric_validation"


def test_cycle_and_unselected_charts_are_not_marked_complete():
    doc = source()
    doc["dependencies"].append(
        {"from": "dataset:shared", "to": CHART, "relation": "cycle"}
    )
    assert any(
        "dependency cycle" in reason for reason in assess(doc)["charts"][0]["reasons"]
    )
    bound = binding()
    bound["charts"] = []
    report = assess(bound=bound)
    assert report["status"] == "blocked"
    assert report["charts"][0]["reasons"] == ["no explicit DOSI binding"]


def test_existing_target_can_disclose_an_unneeded_historical_etl_gap_without_claiming_source_completion():
    doc = source()
    doc["manifest"] = {"status": "partial"}
    doc["assets"].append(
        {
            "id": "external:legacy",
            "kind": "external_source",
            "name": "Legacy table",
            "source": {},
            "definition": {},
            "status": "partial",
        }
    )
    doc["dependencies"].append(
        {"from": "dataset:shared", "to": "external:legacy", "relation": "reads"}
    )
    doc["coverage"] = [
        {
            "asset_id": "external:legacy",
            "status": "partial",
            "code": "external_etl_unresolved",
            "message": "Historical ETL not supplied",
        }
    ]
    assert assess(doc)["status"] == "blocked"
    bound = binding()
    bound["charts"][0]["source_gap_resolutions"] = [
        {
            "asset_id": "external:legacy",
            "code": "external_etl_unresolved",
            "resolution": "existing_dosi_target",
            "evidence": "The discovered warehouse metric uses an existing independently populated target dataset.",
            "metric_bindings": [
                {
                    "metric": catalog()[0]["metric"],
                    "datasource": "warehouse",
                    "model_revision": MODEL_HASH,
                }
            ],
        }
    ]
    report = assess(doc, bound)
    assert report["status"] == "ready_for_metric_validation"
    assert report["source_status"] == "partial"
    note = report["charts"][0]["source_gap_resolutions"][0]
    assert note["source_status"] == "historical_definition_not_synchronized"
    assert note["target_validation"] == "pending_metric_engine_trial"
    assert report["validation"]["metric_engine_executed"] is False
    bound["charts"][0]["source_gap_resolutions"][0]["metric_bindings"][0][
        "model_revision"
    ] = "e" * 64
    assert assess(doc, bound)["status"] == "blocked"
    bound["charts"][0]["source_gap_resolutions"][0]["metric_bindings"][0][
        "model_revision"
    ] = MODEL_HASH
    doc["coverage"].append(
        {
            "asset_id": "external:legacy",
            "status": "error",
            "code": "metadata_failed",
            "message": "Metadata unavailable",
        }
    )
    assert assess(doc, bound)["status"] == "blocked"


def test_multi_metric_join_is_blocked_until_runtime_grain_and_key_proof():
    bound = binding()
    second = copy.deepcopy(bound["charts"][0]["queries"][0])
    second["name"] = "second_metric"
    bound["charts"][0]["queries"].append(second)
    report = assess(bound=bound)
    assert report["status"] == "blocked"
    assert "grain/key validation" in report["charts"][0]["reasons"][0]
    bound["charts"][0]["combination"] = "independent"
    assert len(assess(bound=bound)["charts"][0]["tool_calls"]) == 2


def test_real_source_bundle_checksums_are_verified_before_metric_handoff(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "source"
    manifest = {
        "schema_version": 1,
        "platform": "tableau",
        "source": {"server_url": "https://bi.example.test", "site_content_url": ""},
        "selection": {"dashboard_ids": ["dashboard:workbook-1:Overview"]},
        "exported_at": "2026-11-03T00:00:00Z",
        "status": "ok",
        "summary": {},
    }
    write_bundle(path, manifest, source(), {})
    bound = binding()
    bound["source_manifest_sha256"] = hashlib.sha256(
        (path / "manifest.json").read_bytes()
    ).hexdigest()
    (tmp_path / "bindings.json").write_text(json.dumps(bound))
    (tmp_path / "catalog.json").write_text(json.dumps(catalog()))
    assert (
        main(
            [
                "--bundle",
                "source",
                "--bindings",
                "bindings.json",
                "--metric-catalog",
                "catalog.json",
            ]
        )
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["charts"][0]["status"]
        == "ready_for_metric_validation"
    )
    (path / "assets.json").write_text("[]")
    with pytest.raises(PluginError, match="checksum mismatch"):
        plan_rebuild(path, bindings=bound, metric_catalog=catalog())
    assert (
        main(
            [
                "--bundle",
                "source",
                "--bindings",
                "bindings.json",
                "--metric-catalog",
                "catalog.json",
            ]
        )
        == 1
    )
    assert "checksum mismatch" in capsys.readouterr().err


def test_cli_does_not_read_binding_files_outside_the_workspace(
    tmp_path, monkeypatch, capsys
):
    workspace = tmp_path / "project"
    workspace.mkdir()
    private = tmp_path / "private.json"
    private.write_text("not a catalogue")
    monkeypatch.chdir(workspace)
    assert (
        main(
            [
                "--bundle",
                "source",
                "--bindings",
                str(private),
                "--metric-catalog",
                str(private),
            ]
        )
        == 1
    )
    assert "workspace" in capsys.readouterr().err
