from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import multiprocessing
import os
from pathlib import Path
import threading

import pytest

from datus_tableau_plugin.bundle import inspect_bundle, write_bundle
from datus_tableau_plugin.errors import PluginError, UsageError
from datus_tableau_plugin.rebuild import plan_rebuild


def manifest(tag, source="one"):
    return {
        "schema_version": 1,
        "platform": "tableau",
        "source": {"site": source},
        "selection": {},
        "exported_at": "2026-11-03T00:00:00Z",
        "status": "ok",
        "summary": {"tag": tag},
    }


def documents():
    return {"assets": [], "dependencies": [], "queries": [], "coverage": []}


def store(target, tag, source="one", overwrite=False):
    return write_bundle(
        Path(target),
        manifest(tag, source),
        documents(),
        {"payload.txt": tag.encode()},
        overwrite=overwrite,
    )


def process_writer(target, tag, entered, release, done, results, crash=False):
    from datus_tableau_plugin import bundle

    os.chdir(Path(target).parent)
    replace = bundle.os.replace

    def paused_replace(source, destination):
        result = replace(source, destination)
        if Path(source) == Path(target):
            entered.set()
            if crash:
                os._exit(23)
            if not release.wait(15):
                raise RuntimeError("test writer release timed out")
        return result

    bundle.os.replace = paused_replace
    try:
        store(target, tag, overwrite=True)
        results.put(("ok", tag))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        done.set()


def process_reader(target, started, done, results):
    started.set()
    try:
        data = inspect_bundle(target)
        results.put(("ok", data["manifest"]["summary"]["tag"]))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        done.set()


@pytest.fixture
def target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "snapshot"
    store(path, "initial")
    return path


def test_process_reader_and_writer_wait_for_inflight_snapshot_exchange(target):
    context = multiprocessing.get_context("spawn")
    a_entered, release, a_done = context.Event(), context.Event(), context.Event()
    a_results = context.Queue()
    writer = context.Process(
        target=process_writer,
        args=(str(target), "first", a_entered, release, a_done, a_results),
    )
    reader_started, reader_done = context.Event(), context.Event()
    reader_results = context.Queue()
    reader = context.Process(
        target=process_reader,
        args=(str(target), reader_started, reader_done, reader_results),
    )
    b_entered, b_done = context.Event(), context.Event()
    b_results = context.Queue()
    second = context.Process(
        target=process_writer,
        args=(str(target), "second", b_entered, release, b_done, b_results),
    )
    processes = [writer, reader, second]
    try:
        writer.start()
        assert a_entered.wait(10)
        reader.start()
        second.start()
        assert reader_started.wait(10)
        assert not reader_done.wait(0.2)
        assert not b_entered.wait(0.2)
        release.set()
        assert a_done.wait(10) and b_done.wait(10) and reader_done.wait(10)
        assert a_results.get(timeout=2) == ("ok", "first")
        assert b_results.get(timeout=2) == ("ok", "second")
        read_result = reader_results.get(timeout=2)
        assert read_result[0] == "ok" and read_result[1] in {"first", "second"}
        assert inspect_bundle(target)["manifest"]["summary"]["tag"] == "second"
    finally:
        release.set()
        for process in processes:
            if process.pid:
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)


def test_thread_reader_and_writer_use_the_same_serialization_boundary(
    target, monkeypatch
):
    from datus_tableau_plugin import bundle

    entered, release = threading.Event(), threading.Event()
    replace = bundle.os.replace

    def paused_replace(source, destination):
        result = replace(source, destination)
        if Path(source) == target and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(bundle.os, "replace", paused_replace)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(store, target, "first", overwrite=True)
        try:
            assert entered.wait(5)
            read = executor.submit(inspect_bundle, target)
            second = executor.submit(store, target, "second", overwrite=True)
            assert not read.done() and not second.done()
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
        assert read.result(timeout=5)["manifest"]["summary"]["tag"] in {
            "first",
            "second",
        }
    assert inspect_bundle(target)["manifest"]["summary"]["tag"] == "second"


def test_hard_crash_backup_is_read_without_mutation_and_writer_restores_it(target):
    context = multiprocessing.get_context("spawn")
    entered, release, done, results = (
        context.Event(),
        context.Event(),
        context.Event(),
        context.Queue(),
    )
    process = context.Process(
        target=process_writer,
        args=(str(target), "new", entered, release, done, results, True),
    )
    process.start()
    process.join(10)
    assert process.exitcode == 23
    assert not target.exists()
    before = {
        str(path.relative_to(target.parent)): path.read_bytes()
        for path in target.parent.rglob("*")
        if path.is_file()
    }
    recovered = inspect_bundle(target)
    assert recovered["manifest"]["summary"]["tag"] == "initial"
    assert recovered["recovered_from_backup"] is True
    assert Path(recovered["resolved_path"]).is_dir()
    assert (
        recovered["manifest_sha256"]
        == hashlib.sha256(
            (Path(recovered["resolved_path"]) / "manifest.json").read_bytes()
        ).hexdigest()
    )
    assert not target.exists()
    after = {
        str(path.relative_to(target.parent)): path.read_bytes()
        for path in target.parent.rglob("*")
        if path.is_file()
    }
    assert before == after
    report = plan_rebuild(
        target,
        bindings={
            "schema_version": 1,
            "source_manifest_sha256": recovered["manifest_sha256"],
            "charts": [],
        },
        metric_catalog=[],
    )
    assert report["summary"]["total"] == 0
    store(target, "replacement", overwrite=True)
    assert inspect_bundle(target)["manifest"]["summary"]["tag"] == "replacement"
    assert not any(
        p.name.startswith(".snapshot-previous-") for p in target.parent.iterdir()
    )


def test_invalid_and_symlink_backups_are_not_used(target, tmp_path):
    backup = target.parent / ".snapshot-previous-test"
    target.rename(backup)
    (backup / "payload.txt").write_text("corrupt")
    with pytest.raises(PluginError, match="invalid snapshot recovery"):
        inspect_bundle(target)
    with pytest.raises(PluginError, match="invalid snapshot recovery"):
        store(target, "replacement", overwrite=True)
    assert not target.exists()
    backup.rename(tmp_path / "invalid-backup")
    backup.symlink_to(tmp_path / "invalid-backup", target_is_directory=True)
    with pytest.raises(PluginError, match="unsafe snapshot recovery"):
        inspect_bundle(target)


def test_ambiguous_or_different_source_backups_are_not_guessed(target):
    backup = target.parent / ".snapshot-previous-first"
    target.rename(backup)
    with pytest.raises(UsageError, match="another source"):
        store(target, "replacement", source="different", overwrite=True)
    assert not target.exists()
    second = target.parent / ".snapshot-previous-second"
    store(second, "second", source="different")
    with pytest.raises(PluginError, match="ambiguous"):
        inspect_bundle(target)
    assert backup.is_dir() and second.is_dir() and not target.exists()
