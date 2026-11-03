"""Checksummed, platform-neutral source bundles; no credential or runtime broker dependencies."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from .errors import PluginError, UsageError

DOCUMENTS = {
    "assets": "assets.json",
    "dependencies": "dependencies.json",
    "queries": "queries.json",
    "coverage": "coverage.json",
}
REDACTED = "[REDACTED]"
_SECRET = re.compile(
    r"password|passwd|secret|token|credential|authorization|cookie|private.?key", re.I
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if _SECRET.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> Path:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or path.as_posix() != value
        or path == Path(".")
    ):
        raise UsageError(
            "bundle paths must be canonical relative paths without traversal"
        )
    return path


def workspace_path(value: str | Path) -> Path:
    candidate = Path(value).absolute()
    root = Path.cwd().resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise UsageError("bundle path must be a directory inside the current workspace")
    # A parent symlink could redirect a later atomic rename outside the workspace.
    current = candidate
    while current != current.parent:
        if current.is_symlink():
            raise UsageError("bundle paths cannot contain symbolic links")
        if current == root:
            break
        current = current.parent
    return resolved


def inspect_bundle(path: str | Path) -> dict:
    candidate = Path(path).absolute()
    if candidate.is_symlink():
        raise UsageError("bundle directory cannot be a symbolic link")
    root = candidate.resolve()
    if not root.is_dir():
        raise UsageError("bundle directory does not exist")
    try:
        if (root / "manifest.json").is_symlink():
            raise PluginError("bundle manifest cannot be a symbolic link")
        manifest = json.loads((root / "manifest.json").read_text())
        schema = json.loads(
            (Path(__file__).parent / "schemas/source-bundle-v1.json").read_text()
        )
        import jsonschema

        jsonschema.validate(manifest, schema)
        files = manifest["files"]
        for name, expected in files.items():
            relative = safe_relative(name)
            target = root / relative
            if (
                target.is_symlink()
                or not target.resolve().is_relative_to(root)
                or not target.is_file()
            ):
                raise PluginError(f"bundle file is missing or unsafe: {name}")
            if digest(target.read_bytes()) != expected:
                raise PluginError(f"bundle checksum mismatch: {name}")
        if set(DOCUMENTS.values()) - files.keys():
            raise PluginError("bundle is missing a required document")
        result = {"manifest": manifest}
        document_schema = json.loads(
            (Path(__file__).parent / "schemas/assets-v1.json").read_text()
        )
        for key, filename in DOCUMENTS.items():
            result[key] = json.loads((root / filename).read_text())
        jsonschema.validate({key: result[key] for key in DOCUMENTS}, document_schema)
        ids = [asset["id"] for asset in result["assets"]]
        if len(ids) != len(set(ids)):
            raise PluginError("bundle contains duplicate asset identifiers")
        asset_ids = set(ids)
        for edge in result["dependencies"]:
            if edge["from"] not in asset_ids or edge["to"] not in asset_ids:
                raise PluginError("bundle dependency refers to an unknown asset")
        query_ids = [query["id"] for query in result["queries"]]
        if len(query_ids) != len(set(query_ids)):
            raise PluginError("bundle contains duplicate query identifiers")
        for item in result["coverage"]:
            if item["asset_id"] not in asset_ids:
                raise PluginError("coverage refers to an unknown asset")
        for query in result["queries"]:
            if query["asset_id"] not in asset_ids or (
                query.get("file") and query["file"] not in files
            ):
                raise PluginError("query has an unknown asset or untracked file")
        return result
    except PluginError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PluginError(f"invalid source bundle: {type(exc).__name__}") from None
    except Exception as exc:
        if type(exc).__module__.startswith("jsonschema"):
            raise PluginError(
                "source bundle does not match the version 1 schema"
            ) from None
        raise


def write_bundle(
    path: Path,
    manifest: dict,
    documents: dict,
    extra_files: dict[str, bytes],
    *,
    overwrite: bool = False,
) -> dict:
    target = workspace_path(path)
    if target.exists() and not overwrite:
        raise UsageError(
            "bundle already exists; use --overwrite to update its snapshot"
        )
    if target.exists():
        previous = inspect_bundle(target)
        if previous["manifest"].get("source") != manifest.get("source"):
            raise UsageError(
                "cannot overwrite a snapshot from another Tableau server or site"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    backup: Path | None = None
    try:
        files = {
            filename: json_bytes(documents[key]) for key, filename in DOCUMENTS.items()
        }
        files.update(extra_files)
        for name, content in files.items():
            relative = safe_relative(name)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        manifest = {
            **manifest,
            "files": {name: digest(content) for name, content in sorted(files.items())},
        }
        (staging / "manifest.json").write_bytes(json_bytes(manifest))
        inspect_bundle(staging)
        if target.exists():
            backup = Path(
                tempfile.mkdtemp(prefix=f".{target.name}-previous-", dir=target.parent)
            )
            backup.rmdir()
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except BaseException:
            if backup:
                os.replace(backup, target)
                backup = None
            raise
        if backup:
            shutil.rmtree(backup)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)
