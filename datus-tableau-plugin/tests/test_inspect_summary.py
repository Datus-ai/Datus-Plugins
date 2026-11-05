"""Bounded output is a projection of complete, locked snapshot verification."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from datus_tableau_plugin import bundle as bundle_module, cli
from datus_tableau_plugin.bundle import inspect_bundle, write_bundle
from datus_tableau_plugin.transforms import generate_transforms
from test_managed_offline_contract import forbid_authentication as forbid_authentication


def asset(identifier, kind, name):
    return {
        "id": identifier,
        "kind": kind,
        "name": name,
        "source": {},
        "definition": {},
        "status": "ok",
    }


def snapshot_files(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def store_snapshot(root, *, documents=None, source=None, selection=None, extra=None):
    transforms = [
        asset("transform:one", "transform", "source"),
        asset("transform:two", "transform", "blocked"),
    ]
    transforms[0]["definition"] = {
        "operation": {"type": "source", "sql": "SELECT 1 AS marker"}
    }
    transforms[1]["definition"] = {
        "operation": {"type": "unsupported"},
        "inputs": ["transform:one"],
    }
    transforms[1]["status"] = "unsupported"
    generated = generate_transforms(transforms, [])
    default_documents = {
        "assets": [
            asset("dashboard:book:Overview", "dashboard", "Overview"),
            asset("chart:book:Revenue", "chart", "Revenue"),
            asset("dataset:shared", "dataset", "Sales"),
            *transforms,
        ],
        "dependencies": [
            {
                "from": "dashboard:book:Overview",
                "to": "chart:book:Revenue",
                "relation": "contains",
            },
            {"from": "chart:book:Revenue", "to": "dataset:shared", "relation": "reads"},
            {"from": "dataset:shared", "to": "transform:two", "relation": "reads"},
            {"from": "transform:two", "to": "transform:one", "relation": "depends_on"},
        ],
        "queries": [
            {
                "id": "query:source",
                "asset_id": "dataset:shared",
                "origin": "source",
                "language": "sql",
                "text": "SELECT 1 AS source_marker",
                "file": "sql/source.sql",
                "status": "ok",
            }
        ],
        "coverage": [
            {
                "asset_id": "dataset:shared",
                "status": "partial",
                "code": "external_etl_unresolved",
                "message": "upstream definition required",
            },
            {
                "asset_id": "transform:two",
                "status": "unsupported",
                "code": "processing_model_blocked",
                "message": "unmapped operation",
            },
        ],
    }
    docs = default_documents if documents is None else documents
    extras = {
        "pipeline.json": json.dumps(generated["pipeline"]).encode(),
        "validation.json": json.dumps(generated["validation"]).encode(),
        "native/definition.xml": b"<synthetic>native_definition_marker</synthetic>",
        "sql/source.sql": b"SELECT 1 AS source_marker\n",
        **{name: sql.encode() for name, sql in generated["models"].items()},
    }
    extras.update(extra or {})
    path = root / "snapshot"
    manifest = write_bundle(
        path,
        {
            "schema_version": 1,
            "platform": "tableau",
            "source": {
                "server_url": "https://tableau.example.test",
                "site_content_url": "sales",
            }
            if source is None
            else source,
            "selection": {
                "dashboard_ids": ["dashboard:book:Overview"],
                "project_ids": [],
                "all": False,
            }
            if selection is None
            else selection,
            "exported_at": "2026-11-05T00:00:00Z",
            "status": "partial",
            "summary": {"failures": 0},
        },
        docs,
        extras,
    )
    return path, docs, manifest


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return store_snapshot(tmp_path)


def replace_tracked_document(path, filename, document):
    content = json.dumps(document).encode()
    (path / filename).write_bytes(content)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["files"][filename] = hashlib.sha256(content).hexdigest()
    (path / "manifest.json").write_text(json.dumps(manifest))


def test_summary_has_exact_counts_processing_and_provenance_without_native_content(
    snapshot, capsys
):
    path, documents, manifest = snapshot
    before = snapshot_files(path)
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert set(result) == {
        "schema_version",
        "summary_format",
        "status",
        "source",
        "selection",
        "summary",
        "manifest_sha256",
        "resolved_path",
        "recovered_from_backup",
        "asset_kinds",
        "charts",
        "coverage",
        "processing",
        "omitted_counts",
        "truncation",
        "limits",
        "truncated",
    }
    assert (result["schema_version"], result["summary_format"], result["status"]) == (
        1,
        "tableau-source-summary-v1",
        "partial",
    )
    assert result["source"] == {
        "server_url": "https://tableau.example.test",
        "site_content_url": "sales",
    }
    assert result["selection"] == {
        "dashboard_ids": ["dashboard:book:Overview"],
        "project_ids": [],
        "all": False,
    }
    assert result["summary"] == {
        "assets": 5,
        "dashboards": 1,
        "charts": 1,
        "queries": 1,
        "dependencies": 4,
        "coverage_issues": 2,
    }
    assert result["asset_kinds"] == {
        "chart": 1,
        "dashboard": 1,
        "dataset": 1,
        "transform": 2,
    }
    assert result["charts"] == [{"id": "chart:book:Revenue", "name": "Revenue"}]
    assert result["coverage"] == {
        "status_counts": {"partial": 1, "unsupported": 1},
        "codes": [
            {"code": "external_etl_unresolved", "count": 1},
            {"code": "processing_model_blocked", "count": 1},
        ],
    }
    assert result["processing"] == {
        "pipeline": {
            "status": "partial",
            "execution": "not_run",
            "dialect": "duckdb",
            "nodes": 2,
            "node_status_counts": {"blocked": 1, "generated": 1},
        },
        "validation": {"generated": 1, "blocked": 1, "execution": "not_run"},
    }
    assert result["omitted_counts"] == {
        "charts": 0,
        "coverage_codes": 0,
        "dashboard_ids": 0,
        "project_ids": 0,
        "source_fields": 0,
        "selection_fields": 0,
        "processing_documents": 0,
    }
    assert result["limits"] == {
        "json_bytes": 4096,
        "items_per_list": 10,
        "text_chars": 64,
        "processing_document_bytes": 8388608,
    }
    assert (
        result["manifest_sha256"]
        == hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()
    )
    assert result["recovered_from_backup"] is False
    assert len(result["resolved_path"]) <= 64
    assert result["truncation"] == {
        "text_values": int(len(str(path)) > 64),
        "unsupported_values": 0,
    }
    assert result["truncated"] == (len(str(path)) > 64)
    assert len(captured.out.encode()) <= 4096
    assert "source_marker" not in captured.out
    assert "native_definition_marker" not in captured.out
    assert "unmapped operation" not in captured.out
    assert snapshot_files(path) == before


def test_default_full_inspect_json_and_table_keep_existing_unabridged_contract(
    snapshot, capsys
):
    path, documents, manifest = snapshot
    before = snapshot_files(path)
    assert cli.main(["sync", "inspect", str(path)], {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    full = json.loads(captured.out)
    assert full == {
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(
            (path / "manifest.json").read_bytes()
        ).hexdigest(),
        "resolved_path": str(path),
        **documents,
        "recovered_from_backup": False,
    }
    assert cli.main(["sync", "inspect", str(path), "-o", "table"], {}) == 0
    table = capsys.readouterr()
    assert table.err == ""
    assert "SELECT 1 AS source_marker" in table.out
    assert "external_etl_unresolved" in table.out
    assert snapshot_files(path) == before


def test_summary_rejects_table_format_with_usage_exit_without_reading_or_auth(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    assert (
        cli.main(["sync", "inspect", "does-not-exist", "--summary", "-o", "table"], {})
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: --summary requires --output json\n"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "failure,error",
    [
        ("hidden-sql", "bundle checksum mismatch: sql/source.sql"),
        ("pipeline", "bundle checksum mismatch: pipeline.json"),
        ("schema", "source bundle does not match the version 1 schema"),
        ("duplicate-id", "bundle contains duplicate asset identifiers"),
        ("edge", "bundle dependency refers to an unknown asset"),
        ("coverage", "coverage refers to an unknown asset"),
        ("query-file", "query has an unknown asset or untracked file"),
    ],
)
def test_summary_validates_all_tracked_files_schema_and_graph_before_output(
    snapshot, failure, error, capsys
):
    path, documents, manifest = snapshot
    if failure == "hidden-sql":
        (path / "sql/source.sql").write_bytes(b"changed hidden source SQL")
    elif failure == "pipeline":
        (path / "pipeline.json").write_bytes(b"changed processing")
    elif failure == "schema":
        altered = deepcopy(documents["assets"])
        altered[0]["kind"] = "unknown-kind"
        replace_tracked_document(path, "assets.json", altered)
    elif failure == "duplicate-id":
        replace_tracked_document(
            path, "assets.json", [*documents["assets"], documents["assets"][0]]
        )
    elif failure == "edge":
        replace_tracked_document(
            path,
            "dependencies.json",
            [
                *documents["dependencies"],
                {"from": "chart:book:Revenue", "to": "missing", "relation": "reads"},
            ],
        )
    elif failure == "coverage":
        replace_tracked_document(
            path,
            "coverage.json",
            [
                {
                    "asset_id": "missing",
                    "status": "partial",
                    "code": "gap",
                    "message": "missing identity",
                }
            ],
        )
    else:
        altered = deepcopy(documents["queries"])
        altered[0]["file"] = "untracked.sql"
        replace_tracked_document(path, "queries.json", altered)
    before = snapshot_files(path)
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {error}\n"
    assert snapshot_files(path) == before


def test_summary_caps_each_list_and_reports_exact_omissions(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    charts = [asset(f"chart:{i:02}", "chart", f"Chart {i:02}") for i in range(12)]
    documents = {
        "assets": charts,
        "dependencies": [],
        "queries": [],
        "coverage": [
            {
                "asset_id": charts[i]["id"],
                "status": "partial",
                "code": f"gap-{i:02}",
                "message": "gap",
            }
            for i in range(12)
        ],
    }
    selection = {
        "dashboard_ids": [f"dashboard:{i:02}" for i in range(12)],
        "project_ids": [f"project:{i:02}" for i in range(12)],
        "all": False,
        "private_selection": "not_displayed",
    }
    path, _, _ = store_snapshot(
        tmp_path,
        documents=documents,
        source={
            "server_url": "https://tableau.example.test",
            "site_content_url": "sales",
            "extension_one": "not_displayed",
            "extension_two": "not_displayed",
        },
        selection=selection,
    )
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["charts"] == [
        {"id": f"chart:{i:02}", "name": f"Chart {i:02}"} for i in range(10)
    ]
    assert result["coverage"]["codes"] == [
        {"code": f"gap-{i:02}", "count": 1} for i in range(10)
    ]
    assert result["selection"]["dashboard_ids"] == [
        f"dashboard:{i:02}" for i in range(10)
    ]
    assert result["selection"]["project_ids"] == [f"project:{i:02}" for i in range(10)]
    assert result["omitted_counts"] == {
        "charts": 2,
        "coverage_codes": 2,
        "dashboard_ids": 2,
        "project_ids": 2,
        "source_fields": 2,
        "selection_fields": 1,
        "processing_documents": 0,
    }
    assert result["summary"] == {
        "assets": 12,
        "dashboards": 0,
        "charts": 12,
        "queries": 0,
        "dependencies": 0,
        "coverage_issues": 12,
    }
    assert result["truncated"] is True
    assert "not_displayed" not in captured.out
    assert len(captured.out.encode()) <= 4096


def test_summary_unicode_text_and_total_bytes_are_bounded_with_honest_counts(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    charts = [asset(f"chart:{i:02}", "chart", "图" * 100) for i in range(37)]
    documents = {
        "assets": charts,
        "dependencies": [],
        "queries": [],
        "coverage": [
            {
                "asset_id": charts[i]["id"],
                "status": "partial",
                "code": f"gap-{i:02}",
                "message": "图" * 10000,
            }
            for i in range(29)
        ],
    }
    path, _, _ = store_snapshot(
        tmp_path,
        documents=documents,
        source={"server_url": "源" * 100, "site_content_url": "站" * 100},
        selection={
            "dashboard_ids": ["盘" * 100 + str(i) for i in range(23)],
            "project_ids": ["项" * 100 + str(i) for i in range(19)],
            "all": False,
        },
    )
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert len(captured.out.encode("utf-8")) <= 4096
    assert result["source"] == {
        "server_url": "源" * 63 + "…",
        "site_content_url": "站" * 63 + "…",
    }
    assert result["summary"] == {
        "assets": 37,
        "dashboards": 0,
        "charts": 37,
        "queries": 0,
        "dependencies": 0,
        "coverage_issues": 29,
    }
    assert len(result["charts"]) <= 10
    assert len(result["coverage"]["codes"]) <= 10
    assert len(result["selection"]["dashboard_ids"]) <= 10
    assert len(result["selection"]["project_ids"]) <= 10
    assert len(result["charts"]) + result["omitted_counts"]["charts"] == 37
    assert (
        len(result["coverage"]["codes"]) + result["omitted_counts"]["coverage_codes"]
        == 29
    )
    assert (
        len(result["selection"]["dashboard_ids"])
        + result["omitted_counts"]["dashboard_ids"]
        == 23
    )
    assert (
        len(result["selection"]["project_ids"])
        + result["omitted_counts"]["project_ids"]
        == 19
    )
    assert result["truncation"] == {
        "text_values": 32 + int(len(str(path)) > 64),
        "unsupported_values": 0,
    }
    assert result["truncated"] is True


def test_summary_uses_processing_bytes_captured_once_under_the_read_lock(
    snapshot, monkeypatch
):
    path, documents, manifest = snapshot
    original_guard = bundle_module._directory_guard
    original_read = Path.read_bytes
    state = {"locked": False}
    reads = []

    @contextmanager
    def guard(parent, *, exclusive):
        with original_guard(parent, exclusive=exclusive):
            state["locked"] = True
            try:
                yield
            finally:
                state["locked"] = False

    def read(target):
        if target.name in {"pipeline.json", "validation.json"}:
            reads.append((target.name, state["locked"]))
        return original_read(target)

    monkeypatch.setattr(bundle_module, "_directory_guard", guard)
    monkeypatch.setattr(Path, "read_bytes", read)
    result = inspect_bundle(path, summary=True)
    assert sorted(reads) == [("pipeline.json", True), ("validation.json", True)]
    assert state["locked"] is False
    assert result["processing"]["validation"] == {
        "generated": 1,
        "blocked": 1,
        "execution": "not_run",
    }


def test_summary_omits_oversized_processing_body_but_still_checks_its_hash(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    payload = b'{"nodes":[],"padding":"' + b"x" * 8388608 + b'"}'
    path, _, _ = store_snapshot(tmp_path, extra={"pipeline.json": payload})
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["processing"]["pipeline"] == {
        "summary_omitted": True,
        "bytes": len(payload),
    }
    assert result["omitted_counts"]["processing_documents"] == 1
    assert result["processing"]["validation"] == {
        "generated": 1,
        "blocked": 1,
        "execution": "not_run",
    }
    assert len(captured.out.encode()) <= 4096
    (path / "pipeline.json").write_bytes(payload + b" ")
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 1
    failed = capsys.readouterr()
    assert failed.out == ""
    assert failed.err == "error: bundle checksum mismatch: pipeline.json\n"


@pytest.mark.parametrize(
    "content,error",
    [
        (b"[]", "ValueError"),
        (b'{"nodes":{}}', "ValueError"),
        (b'{"nodes":[1]}', "ValueError"),
        (b"not-json", "JSONDecodeError"),
    ],
)
def test_summary_malformed_processing_has_controlled_failure(
    tmp_path, monkeypatch, capsys, content, error
):
    monkeypatch.chdir(tmp_path)
    path, _, _ = store_snapshot(tmp_path, extra={"pipeline.json": content})
    before = snapshot_files(path)
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: invalid source bundle: {error}\n"
    assert snapshot_files(path) == before


def test_summary_sanitizes_unsupported_scalars_and_processing_counts(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    path, _, _ = store_snapshot(
        tmp_path,
        source={"server_url": {}, "site_content_url": 3},
        selection={
            "dashboard_ids": "not-list",
            "project_ids": [42, None],
            "all": "yes",
        },
        extra={
            "pipeline.json": json.dumps(
                {
                    "status": True,
                    "execution": {},
                    "dialect": 7,
                    "nodes": [{"status": "future"}, {"status": "generated"}],
                }
            ).encode(),
            "validation.json": b'{"generated":true,"blocked":-1,"execution":"not_run"}',
        },
    )
    result = inspect_bundle(path, summary=True)
    assert result["source"] == {"server_url": None, "site_content_url": None}
    assert result["selection"] == {
        "dashboard_ids": [],
        "project_ids": [None, None],
        "all": None,
    }
    assert result["processing"] == {
        "pipeline": {
            "status": None,
            "execution": None,
            "dialect": None,
            "nodes": 2,
            "node_status_counts": {"generated": 1, "unknown": 1},
        },
        "validation": {"generated": None, "blocked": None, "execution": "not_run"},
    }
    assert result["truncation"] == {
        "text_values": int(len(str(path)) > 64),
        "unsupported_values": 10,
    }
    assert result["truncated"] is True


def test_summary_reads_verified_recovery_backup_without_restoring_or_changing_files(
    snapshot, capsys
):
    path, documents, manifest = snapshot
    backup = path.parent / ".snapshot-previous-test"
    path.rename(backup)
    before = snapshot_files(path.parent)
    manifest_hash = hashlib.sha256((backup / "manifest.json").read_bytes()).hexdigest()
    assert cli.main(["sync", "inspect", str(path), "--summary"], {}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["recovered_from_backup"] is True
    assert result["manifest_sha256"] == manifest_hash
    assert result["processing"]["validation"] == {
        "generated": 1,
        "blocked": 1,
        "execution": "not_run",
    }
    assert not path.exists()
    assert backup.is_dir()
    assert snapshot_files(path.parent) == before
