from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading

import pytest

from datus_tableau_plugin import bundle as bundle_module
from datus_tableau_plugin.bundle import inspect_bundle
from datus_tableau_plugin.errors import PluginError
from datus_tableau_plugin.sync import sync_sources
from test_sync import Client


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def snapshot_bytes(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_failed_sync_cannot_replace_crash_backup(workspace):
    client = Client()
    initial = sync_sources(client, all_dashboards=True)
    target = Path(initial["output_dir"])
    original = snapshot_bytes(target)
    backup = target.with_name(f".{target.name}-previous-crashed")
    os.replace(target, backup)
    client.failed.add(("metadata", "book1"))
    with pytest.raises(PluginError, match="preserved"):
        sync_sources(client, all_dashboards=True, overwrite=True)
    assert not target.exists()
    assert backup.is_dir()
    assert snapshot_bytes(backup) == original
    assert snapshot_bytes(Path(inspect_bundle(target)["resolved_path"])) == original
    client.failed.clear()
    restored = sync_sources(client, all_dashboards=True, overwrite=True)
    assert restored["status"] == "ok"
    assert target.is_dir() and not backup.exists()


def test_failed_sync_new_target_still_saves_partial(workspace):
    client = Client()
    client.failed.add(("metadata", "book1"))
    result = sync_sources(client, all_dashboards=True, overwrite=True)
    assert inspect_bundle(result["output_dir"])["manifest"]["summary"]["failures"] == 1
    assert result["status"] == "partial"


def test_failed_writer_rechecks_target_after_waiting_for_lock(workspace, monkeypatch):
    initial = sync_sources(Client(), all_dashboards=True)
    target = Path(initial["output_dir"])
    saved = target.with_name("saved-good")
    os.replace(target, saved)
    original = snapshot_bytes(saved)
    entered = threading.Event()
    real_guard = bundle_module._directory_guard

    def notified_guard(parent, *, exclusive):
        if exclusive:
            entered.set()
        return real_guard(parent, exclusive=exclusive)

    monkeypatch.setattr(bundle_module, "_directory_guard", notified_guard)
    failed = Client()
    failed.failed.add(("metadata", "book1"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with real_guard(target.parent, exclusive=True):
            future = executor.submit(
                sync_sources, failed, all_dashboards=True, overwrite=True
            )
            assert entered.wait(5)
            os.replace(saved, target)
        with pytest.raises(PluginError, match="preserved"):
            future.result(timeout=5)
    assert snapshot_bytes(target) == original
