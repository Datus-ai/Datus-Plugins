"""Artifact capture, session export, hashing, and secret redaction."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any


SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|client[_-]?key)", re.I)
SECRET_ENV = re.compile(r"\b[A-Z][A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\b")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("<redacted>" if SECRET_KEY.search(str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = SECRET_ENV.sub("<redacted-env>", value)
        value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", value)
        value = re.sub(r"(?i)((?:password|secret|token|api[_-]?key)\s*[:=]\s*)[^\s,;]+", r"\1<redacted>", value)
        return value
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_session(home: Path, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    dbs = sorted(home.glob("sessions/**/*.db"), key=lambda item: item.stat().st_mtime)
    if not dbs:
        return {"session_db": None, "usage": {}, "messages": 0}
    source = dbs[-1]
    copied = destination / "session.db"
    shutil.copy2(source, copied)
    messages: list[Any] = []
    usage: dict[str, int] = {}
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "agent_messages" in tables:
            for (raw,) in conn.execute("SELECT message_data FROM agent_messages ORDER BY id"):
                try:
                    messages.append(redact(json.loads(raw)))
                except (json.JSONDecodeError, TypeError):
                    messages.append(redact({"raw": str(raw)}))
        if "turn_usage" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(turn_usage)")}
            detail_column = ", input_tokens_details" if "input_tokens_details" in columns else ""
            cached_input_tokens = 0
            totals = [0, 0, 0, 0]
            for row in conn.execute(
                "SELECT requests, input_tokens, output_tokens, total_tokens"
                f"{detail_column} FROM turn_usage"
            ):
                for index, value in enumerate(row[:4]):
                    totals[index] += int(value or 0)
                if detail_column and row[4]:
                    try:
                        details = json.loads(row[4]) if isinstance(row[4], str) else row[4]
                        cached_input_tokens += int(details.get("cached_tokens") or 0)
                    except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
                        pass
            usage = dict(zip(("requests", "input_tokens", "output_tokens", "total_tokens"), totals))
            usage["cached_input_tokens"] = min(cached_input_tokens, usage["input_tokens"])
            usage["uncached_input_tokens"] = usage["input_tokens"] - usage["cached_input_tokens"]
            usage["effective_tokens"] = usage["uncached_input_tokens"] + usage["output_tokens"]
    with (destination / "session.jsonl").open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(json.dumps(message, ensure_ascii=False) + "\n")
    snapshots = sorted(home.glob("sessions/**/*.sysprompt.json"))
    if snapshots:
        try:
            snapshot = redact(json.loads(snapshots[-1].read_text(encoding="utf-8")))
            (destination / "system-prompt.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    return {"session_db": str(copied), "usage": usage, "messages": len(messages)}


def capture_generated(workspace: Path, patterns: tuple[str, ...], destination: Path, baseline: dict[str, str] | None = None) -> list[dict[str, Any]]:
    destination.mkdir(parents=True, exist_ok=True)
    captured: list[dict[str, Any]] = []
    baseline = baseline or {}
    patch_parts: list[str] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for source in sorted(workspace.glob(pattern)):
            if not source.is_file() or source.is_symlink() or source in seen:
                continue
            seen.add(source)
            relative = source.relative_to(workspace)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            captured.append({"path": relative.as_posix(), "sha256": sha256(source), "bytes": source.stat().st_size})
            try:
                after = source.read_text(encoding="utf-8").splitlines(keepends=True)
            except (OSError, UnicodeDecodeError):
                continue
            before = baseline.get(relative.as_posix(), "").splitlines(keepends=True)
            patch_parts.extend(difflib.unified_diff(before, after, fromfile=f"a/{relative}", tofile=f"b/{relative}"))
    (destination.parent / "generated.patch").write_text("".join(patch_parts), encoding="utf-8")
    (destination.parent / "workspace-manifest.json").write_text(json.dumps(captured, indent=2) + "\n", encoding="utf-8")
    return captured


def snapshot_text(workspace: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in workspace.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            result[path.relative_to(workspace).as_posix()] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return result
