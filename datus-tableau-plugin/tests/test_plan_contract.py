"""Offline planner contracts with real native parsing and filesystem boundaries."""

from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from datus_tableau_plugin import plan
from datus_tableau_plugin.errors import UsageError


@pytest.fixture
def flow_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = json.dumps(
        {
            "nodes": {
                "input": {
                    "id": "input",
                    "nodeType": ".v1.LoadCsv",
                    "baseType": "input",
                    "fields": [{"name": "id", "type": "integer"}],
                    "nextNodes": [
                        {
                            "namespace": "Default",
                            "nextNodeId": "derive",
                            "nextNamespace": "Default",
                        }
                    ],
                },
                "derive": {
                    "id": "derive",
                    "nodeType": ".v1.AddColumn",
                    "baseType": "transform",
                    "columnName": "next_id",
                    "expression": "[id] + 2",
                    "nextNodes": [],
                },
            }
        }
    ).encode()
    source = tmp_path / "flow.tfl"
    source.write_bytes(payload)
    bindings = {
        "transform:fixture:input": {"table": "synthetic_source", "columns": ["id"]}
    }
    binding_file = tmp_path / "bindings.json"
    binding_file.write_text(json.dumps(bindings))
    return tmp_path, source, payload, bindings, binding_file


def argv(source, binding_file=None):
    command = ["--flow", str(source), "--flow-id", "fixture", "--output", "plan"]
    if binding_file is not None:
        command += ["--source-bindings", str(binding_file)]
    return command


def test_main_success_writes_verified_artifacts_and_executable_synthetic_selects(
    flow_workspace, capsys
):
    root, source, payload, bindings, binding_file = flow_workspace
    assert plan.main(argv(source, binding_file)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "output_dir": str((root / "plan").resolve()),
        "status": "generated",
        "generated": 2,
        "blocked": 0,
        "source_bindings_required": [],
        "execution": "not_run",
    }
    manifest = json.loads((root / "plan/plan-manifest.json").read_text())
    assert (
        manifest["artifact_kind"],
        manifest["flow_id"],
        manifest["origin"],
        manifest["execution"],
    ) == ("tableau_native_flow_plan", "fixture", "derived", "not_run")
    assert manifest["source_sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(manifest["files"]) == 10
    for filename, checksum in manifest["files"].items():
        content = (root / "plan" / filename).read_bytes()
        assert checksum == {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    pipeline = json.loads((root / "plan/pipeline.json").read_text())
    assert len(pipeline["nodes"]) == 2
    derived = next(
        n for n in pipeline["nodes"] if n["id"] == "transform:fixture:derive"
    )
    assert derived["status"] == "generated"
    connection = duckdb.connect()
    connection.execute("CREATE TABLE synthetic_source(id INTEGER)")
    connection.execute("INSERT INTO synthetic_source VALUES (10),(NULL)")
    assert connection.execute(
        (root / "plan" / derived["file"]).read_text()
    ).fetchall() == [(10, 12), (None, None)]
    assert source.read_bytes() == payload
    assert list(root.glob(".tableau-plan-*")) == []


def test_main_partial_lists_exact_required_mapping_and_returns_nonzero(
    flow_workspace, capsys
):
    root, source, payload, bindings, binding_file = flow_workspace
    assert plan.main(argv(source)) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "output_dir": str((root / "plan").resolve()),
        "status": "partial",
        "generated": 0,
        "blocked": 2,
        "source_bindings_required": [
            {
                "id": "transform:fixture:input",
                "name": "input",
                "columns": ["id"],
                "source_kind": "LoadCsv",
            }
        ],
        "execution": "not_run",
    }
    validation = json.loads((root / "plan/validation.json").read_text())
    assert (
        validation["generated"],
        validation["blocked"],
        validation["execution"],
    ) == (0, 2, "not_run")
    assert list((root / "plan").glob("models/*.sql")) == []
    assert source.read_bytes() == payload


@pytest.mark.parametrize("flow_id", ["invalid/id", "", "x" * 129])
def test_invalid_flow_identity_is_usage_error_before_creating_output(
    flow_workspace, flow_id
):
    root, source, payload, bindings, binding_file = flow_workspace
    with pytest.raises(UsageError, match="flow ID must contain") as exc:
        plan.plan_flow(flow=source, output="plan", flow_id=flow_id)
    assert exc.value.exit_code == 2
    assert source.read_bytes() == payload
    assert not (root / "plan").exists()
    assert list(root.glob(".tableau-plan-*")) == []


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink"])
def test_nonregular_native_inputs_are_rejected_without_writing(flow_workspace, kind):
    root, source, payload, bindings, binding_file = flow_workspace
    invalid = root / "invalid.tfl"
    if kind == "directory":
        invalid.mkdir()
    elif kind == "symlink":
        invalid.symlink_to(source)
    with pytest.raises(UsageError, match="bounded regular native definition") as exc:
        plan.plan_flow(flow=invalid, output="plan")
    assert exc.value.exit_code == 2
    assert source.read_bytes() == payload
    assert not (root / "plan").exists()
    assert list(root.glob(".tableau-plan-*")) == []


def test_main_malformed_json_has_controlled_error_and_no_partial_output(
    flow_workspace, capsys
):
    root, source, payload, bindings, binding_file = flow_workspace
    source.write_bytes(b"{invalid-json")
    with pytest.raises(SystemExit) as exc:
        plan.main(argv(source))
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: invalid Prep flow JSON\n"
    assert source.read_bytes() == b"{invalid-json"
    assert not (root / "plan").exists()
    assert list(root.glob(".tableau-plan-*")) == []


def test_existing_plan_directory_is_preserved_exactly(flow_workspace):
    root, source, payload, bindings, binding_file = flow_workspace
    target = root / "plan"
    target.mkdir()
    sentinel = target / "original.bin"
    sentinel.write_bytes(b"previous-plan-content")
    with pytest.raises(UsageError, match="output already exists") as exc:
        plan.plan_flow(flow=source, output="plan", source_bindings=bindings)
    assert exc.value.exit_code == 2
    assert sorted(p.name for p in target.iterdir()) == ["original.bin"]
    assert sentinel.read_bytes() == b"previous-plan-content"
    assert source.read_bytes() == payload
    assert list(root.glob(".tableau-plan-*")) == []


@pytest.mark.parametrize("failure", ["write", "rename"])
def test_main_filesystem_failure_cleans_staging_and_preserves_input(
    flow_workspace, monkeypatch, capsys, failure
):
    root, source, payload, bindings, binding_file = flow_workspace
    failures = []
    original_write = Path.write_bytes

    def write(path, data):
        if (
            failure == "write"
            and path.name == "pipeline.json"
            and path.parent.name.startswith(".tableau-plan-")
        ):
            failures.append("write")
            raise OSError("synthetic write failure")
        return original_write(path, data)

    def rename(source_path, target_path):
        failures.append("rename")
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(Path, "write_bytes", write)
    if failure == "rename":
        monkeypatch.setattr(plan.os, "rename", rename)
    with pytest.raises(SystemExit) as exc:
        plan.main(argv(source, binding_file))
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: synthetic {failure} failure\n"
    assert failures == [failure]
    assert source.read_bytes() == payload
    assert not (root / "plan").exists()
    assert list(root.glob(".tableau-plan-*")) == []


@pytest.mark.parametrize(
    "kind,message",
    [
        ("directory", "output appeared during generation"),
        ("symlink", "cannot contain symbolic links"),
    ],
)
def test_concurrent_target_is_preserved_and_staging_removed(
    flow_workspace, monkeypatch, kind, message
):
    root, source, payload, bindings, binding_file = flow_workspace
    target = root / "plan"
    competitor = root / "concurrent"
    competitor.mkdir()
    (competitor / "owned.bin").write_bytes(b"concurrent-writer-content")
    original_lock = fcntl.flock
    acquired = []

    def acquire(descriptor, operation):
        original_lock(descriptor, operation)
        acquired.append(operation)
        if kind == "directory":
            competitor.rename(target)
        else:
            target.symlink_to(competitor, target_is_directory=True)

    monkeypatch.setattr(fcntl, "flock", acquire)
    with pytest.raises(UsageError, match=message) as exc:
        plan.plan_flow(
            flow=source, output="plan", flow_id="fixture", source_bindings=bindings
        )
    assert exc.value.exit_code == 2
    assert acquired == [fcntl.LOCK_EX]
    assert (target / "owned.bin").read_bytes() == b"concurrent-writer-content"
    assert sorted(p.name for p in target.iterdir()) == ["owned.bin"]
    assert source.read_bytes() == payload
    assert list(root.glob(".tableau-plan-*")) == []


def test_main_invalid_binding_document_returns_usage_error_without_output(
    flow_workspace, capsys
):
    root, source, payload, bindings, binding_file = flow_workspace
    binding_file.write_text("[]")
    with pytest.raises(SystemExit) as exc:
        plan.main(argv(source, binding_file))
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "error: source bindings must be an object keyed by full transform identity\n"
    )
    assert source.read_bytes() == payload
    assert not (root / "plan").exists()
    assert list(root.glob(".tableau-plan-*")) == []
