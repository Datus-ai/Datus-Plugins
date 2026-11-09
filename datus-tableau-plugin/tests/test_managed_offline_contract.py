"""Managed entrypoints execute real offline modules without a profile or client."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tomllib

import pytest
import yaml

from datus_tableau_plugin import __version__, cli
from datus_tableau_plugin.bundle import write_bundle
from test_plan_contract import flow_workspace as flow_workspace
from test_rebuild_workflow import binding, catalog, source


@pytest.fixture(autouse=True)
def forbid_authentication(monkeypatch):
    calls = []

    def forbidden_profile(*args, **kwargs):
        calls.append("profile")
        raise AssertionError("offline command accessed profile validation")

    def forbidden_client(*args, **kwargs):
        calls.append("client")
        raise AssertionError("offline command constructed a remote client")

    monkeypatch.setattr(cli.Settings, "from_profile", forbidden_profile)
    monkeypatch.setattr(cli, "TableauClient", forbidden_client)
    yield
    assert calls == []


def files_below(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def rebuild_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "source"
    write_bundle(
        bundle,
        {
            "schema_version": 1,
            "platform": "tableau",
            "source": {
                "server_url": "https://tableau.example.test",
                "site_content_url": "",
            },
            "selection": {"dashboard_ids": ["dashboard:workbook-1:Overview"]},
            "exported_at": "2026-11-05T00:00:00Z",
            "status": "ok",
            "summary": {},
        },
        source(),
        {},
    )
    manifest_hash = hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    bindings = binding()
    bindings["source_manifest_sha256"] = manifest_hash
    (tmp_path / "bindings.json").write_text(json.dumps(bindings))
    (tmp_path / "catalog.json").write_text(json.dumps(catalog()))
    return tmp_path, bundle, bindings, manifest_hash


def rebuild_arguments():
    return [
        "rebuild",
        "--bundle",
        "source",
        "--bindings",
        "bindings.json",
        "--metric-catalog",
        "catalog.json",
    ]


def test_managed_plan_writes_checked_models_with_independent_sqlite_results(
    flow_workspace, capsys
):
    root, flow, payload, bindings, binding_file = flow_workspace
    assert (
        cli.main(
            [
                "plan",
                "--flow",
                str(flow),
                "--flow-id",
                "fixture",
                "--source-bindings",
                str(binding_file),
                "--output",
                "managed-plan",
                "--dialect",
                "duckdb",
            ],
            {},
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "output_dir": str(root / "managed-plan"),
        "status": "generated",
        "generated": 2,
        "blocked": 0,
        "source_bindings_required": [],
        "execution": "not_run",
    }
    output = root / "managed-plan"
    manifest = json.loads((output / "plan-manifest.json").read_text())
    assert manifest["source_sha256"] == hashlib.sha256(payload).hexdigest()
    assert (manifest["flow_id"], manifest["artifact_kind"], manifest["execution"]) == (
        "fixture",
        "tableau_native_flow_plan",
        "not_run",
    )
    assert len(manifest["files"]) == 10
    for name, expected in manifest["files"].items():
        content = (output / name).read_bytes()
        assert expected == {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    pipeline = json.loads((output / "pipeline.json").read_text())
    assert [(node["id"], node["status"]) for node in pipeline["nodes"]] == [
        ("transform:fixture:derive", "generated"),
        ("transform:fixture:input", "generated"),
    ]
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE synthetic_source(id INTEGER)")
        database.executemany(
            "INSERT INTO synthetic_source VALUES (?)", [(10,), (None,)]
        )
        sql = (output / pipeline["nodes"][0]["file"]).read_text()
        assert database.execute(sql).fetchall() == [(10, 12), (None, None)]
    assert flow.read_bytes() == payload
    assert json.loads(binding_file.read_text()) == bindings
    assert list(root.glob(".tableau-plan-*")) == []


def test_managed_plan_omitted_binding_preserves_partial_exit_and_default_flow_identity(
    flow_workspace, capsys
):
    root, flow, payload, bindings, binding_file = flow_workspace
    assert cli.main(["plan", "--flow", str(flow), "--output", "partial-plan"], {}) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "output_dir": str(root / "partial-plan"),
        "status": "partial",
        "generated": 0,
        "blocked": 2,
        "source_bindings_required": [
            {
                "id": "transform:local:input",
                "name": "input",
                "columns": ["id"],
                "source_kind": "LoadCsv",
            }
        ],
        "execution": "not_run",
    }
    assert list((root / "partial-plan").glob("models/*.sql")) == []
    assert flow.read_bytes() == payload


@pytest.mark.parametrize(
    "command",
    [
        ["plan"],
        ["rebuild"],
        ["plan", "--flow", "source.tfl"],
        ["rebuild", "--bundle", "source"],
    ],
)
def test_incomplete_offline_arguments_return_usage_code_without_auth(
    command, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    assert cli.main(command, {}) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "required" in captured.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "command,flag",
    [(["plan", "--help"], "--output"), (["rebuild", "--help"], "--metric-catalog")],
)
def test_offline_help_is_available_without_profile(command, flag, capsys):
    assert cli.main(command, {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert flag in captured.out


@pytest.mark.parametrize("failure", ["malformed", "outside", "existing"])
def test_managed_plan_failures_keep_input_and_existing_files(
    flow_workspace, failure, capsys
):
    root, flow, payload, bindings, binding_file = flow_workspace
    output = "plan"
    if failure == "malformed":
        flow.write_bytes(b"{invalid-json")
        expected_error = "invalid Prep flow JSON"
    elif failure == "outside":
        output = "../outside-" + root.name
        expected_error = "bundle path must be a directory inside the current workspace"
    else:
        (root / output).mkdir()
        (root / output / "sentinel.bin").write_bytes(b"existing-plan")
        expected_error = "offline plan output already exists; choose a new directory"
    before = files_below(root)
    assert cli.main(["plan", "--flow", str(flow), "--output", output], {}) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {expected_error}\n"
    assert files_below(root) == before
    assert list(root.glob(".tableau-plan-*")) == []
    assert not (root.parent / ("outside-" + root.name)).exists()


def test_managed_rebuild_is_read_only_preflight_and_never_claims_metric_execution(
    rebuild_workspace, capsys
):
    root, bundle, bindings, manifest_hash = rebuild_workspace
    before = files_below(root)
    assert cli.main(rebuild_arguments(), {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["status"] == "ready_for_metric_validation"
    assert report["source_manifest_sha256"] == manifest_hash
    assert report["summary"] == {"total": 1, "ready": 1, "blocked": 0}
    assert report["validation"] == {
        "metric_engine_executed": False,
        "dashboard_rendered": False,
        "published": False,
    }
    assert report["charts"][0]["tool_calls"] == [
        {
            "tool": "save_metric_query_template",
            "arguments": {
                "name": "revenue_by_region",
                "query": bindings["charts"][0]["queries"][0]["query"],
                "goal": "Compare current net revenue by region",
                "hypothesis": "Regions explain net revenue composition",
                "params": [
                    {"name": "start_date", "type": "date", "required": True},
                    {"name": "region", "type": "string", "required": False},
                ],
                "sample_params": {"start_date": "2026-01-01", "region": "East"},
                "caveats": "",
            },
        }
    ]
    assert "original_sales" not in captured.out
    assert files_below(root) == before


def test_managed_rebuild_missing_mapping_blocks_without_writes(
    rebuild_workspace, capsys
):
    root, bundle, bindings, manifest_hash = rebuild_workspace
    bindings["charts"] = []
    (root / "bindings.json").write_text(json.dumps(bindings))
    before = files_below(root)
    assert cli.main(rebuild_arguments(), {}) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["summary"] == {"total": 1, "ready": 0, "blocked": 1}
    assert report["charts"][0]["reasons"] == ["no explicit DOSI binding"]
    assert report["charts"][0]["tool_calls"] == []
    assert report["validation"] == {
        "metric_engine_executed": False,
        "dashboard_rendered": False,
        "published": False,
    }
    assert files_below(root) == before


@pytest.mark.parametrize("failure", ["checksum", "identity", "outside-input"])
def test_managed_rebuild_invalid_inputs_report_failure_without_success_json(
    rebuild_workspace, failure, capsys
):
    root, bundle, bindings, manifest_hash = rebuild_workspace
    args = rebuild_arguments()
    if failure == "checksum":
        (bundle / "assets.json").write_text("[]")
        expected_error = "bundle checksum mismatch: assets.json"
    elif failure == "identity":
        bindings["source_manifest_sha256"] = "0" * 64
        (root / "bindings.json").write_text(json.dumps(bindings))
        expected_error = (
            "bindings must pin schema_version 1 and the exact source manifest SHA256"
        )
    else:
        args[4] = "../not-in-workspace.json"
        expected_error = "bundle path must be a directory inside the current workspace"
    before = files_below(root)
    assert cli.main(args, {}) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {expected_error}\n"
    assert files_below(root) == before


def test_version_manifest_flags_and_offline_permissions_match_managed_interface():
    root = Path(__file__).parents[1]
    manifest = yaml.safe_load(
        (root / "datus_tableau_plugin/datus-plugin.yml").read_text()
    )
    commands = {item["name"]: item for item in manifest["commands"]}
    assert commands["plan"]["args"] == [
        {"name": "--flow", "required": True},
        {"name": "--flow-id"},
        {"name": "--source-bindings"},
        {"name": "--output", "required": True},
        {"name": "--dialect"},
    ]
    assert commands["rebuild"]["args"] == [
        {"name": "--bundle", "required": True},
        {"name": "--bindings", "required": True},
        {"name": "--metric-catalog", "required": True},
    ]
    inspect = next(
        item for item in commands["sync"]["subcommands"] if item["name"] == "inspect"
    )
    assert inspect["args"] == [
        {"name": "bundle_path", "required": True},
        {"name": "--summary"},
    ]
    for mode in ["normal", "auto"]:
        permissions = manifest["permissions"][mode]
        assert permissions["ask"] == [
            "sync run:*",
            "plan:*",
            "context export-dashboard:*",
            "api call:*",
        ]
        assert "rebuild:*" in permissions["allow"]
        assert "sync inspect:*" in permissions["allow"]
        assert "plan:*" not in permissions["allow"]
        assert ":*" not in permissions["allow"]
    declared_version = tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "version"
    ]
    assert declared_version == __version__
