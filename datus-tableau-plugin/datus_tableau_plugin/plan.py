"""Offline native-flow planning exposed through ``datus tableau plan``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .bindings import apply_source_bindings, read_bindings, required_bindings
from .bundle import _directory_guard, workspace_path
from .errors import UsageError
from .native import MAX_ARCHIVE_BYTES, parse_flow
from .transforms import generate_transforms


def plan_flow(
    *,
    flow: str | Path,
    output: str | Path,
    flow_id="local",
    source_bindings: dict | None = None,
    dialect="duckdb",
) -> dict:
    """Write derived planning artifacts without login, extraction or SQL execution."""
    if not isinstance(flow_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", flow_id
    ):
        raise UsageError(
            "flow ID must contain 1-128 ASCII letters, digits, underscore or hyphen"
        )
    source = Path(flow)
    if (
        source.is_symlink()
        or not source.is_file()
        or source.stat().st_size > MAX_ARCHIVE_BYTES
    ):
        raise UsageError("flow must be a bounded regular native definition file")
    target = workspace_path(output)
    if target.exists():
        raise UsageError("offline plan output already exists; choose a new directory")
    payload = source.read_bytes()
    parsed = parse_flow(payload, flow_id=flow_id)
    assets = apply_source_bindings(parsed["assets"], source_bindings, dialect=dialect)
    generated = generate_transforms(assets, parsed["dependencies"], dialect=dialect)
    generated["pipeline"]["source_bindings_required"] = required_bindings(assets)
    files = {
        "assets.json": assets,
        "dependencies.json": parsed["dependencies"],
        "coverage.json": parsed["coverage"],
        "pipeline.json": generated["pipeline"],
        "validation.json": generated["validation"],
        **generated["transforms"],
    }
    serialized = {
        name: (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        for name, value in files.items()
    }
    serialized.update({name: sql.encode() for name, sql in generated["models"].items()})
    serialized.update(
        {"native/" + name: text.encode() for name, text in parsed["documents"].items()}
    )
    manifest = {
        "schema_version": 1,
        "artifact_kind": "tableau_native_flow_plan",
        "origin": "derived",
        "flow_id": flow_id,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "status": generated["pipeline"]["status"],
        "execution": "not_run",
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(serialized.items())
        },
    }
    serialized["plan-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tableau-plan-", dir=target.parent))
    try:
        for name, data in serialized.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        with _directory_guard(target.parent, exclusive=True):
            workspace_path(output)
            if target.exists() or target.is_symlink():
                raise UsageError("offline plan output appeared during generation")
            os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {
        "output_dir": str(target),
        "status": manifest["status"],
        "generated": generated["validation"]["generated"],
        "blocked": generated["validation"]["blocked"],
        "source_bindings_required": required_bindings(assets),
        "execution": "not_run",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plan a local native Tableau Prep flow; never connects or executes SQL"
    )
    parser.add_argument("--flow", required=True)
    parser.add_argument("--flow-id", default="local")
    parser.add_argument("--source-bindings")
    parser.add_argument("--output", required=True)
    parser.add_argument("--dialect", default="duckdb")
    args = parser.parse_args(argv)
    try:
        result = plan_flow(
            flow=args.flow,
            flow_id=args.flow_id,
            output=args.output,
            source_bindings=read_bindings(args.source_bindings)
            if args.source_bindings
            else None,
            dialect=args.dialect,
        )
    except (UsageError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, indent=2))
    return 1 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
