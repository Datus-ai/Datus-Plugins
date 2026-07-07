"""`datus airflow dags deploy/undeploy` — ship or remove DAG files on the target."""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
from typing import List

from ..deploy import (
    DeployItem,
    capture_import_error_state,
    capture_parse_state,
    collect_files,
    make_target,
    verify_dags,
)
from ..errors import ConfigError, UsageError
from . import Context, confirm


def _resolve_dest(ctx: Context, ns) -> str:
    dest = ns.dest or ctx.settings.dags_folder
    if not dest:
        raise ConfigError(
            "no deployment target: pass --dest or set `dags_folder` in the profile "
            "(e.g. s3://my-bucket/dags/ or /opt/airflow/dags)"
        )
    return dest


def register_deploy(group: argparse._SubParsersAction) -> None:
    p = group.add_parser(
        "deploy",
        help="upload DAG files to the dags folder (s3://... or a local path)",
        description=(
            "Copy DAG files/directories to the deployment target. Directories are "
            "scanned recursively for *.py and *.zip (use --all-files to include "
            "everything). The target comes from --dest or the profile's dags_folder. "
            "With --verify, polls the REST API until the scheduler has re-parsed the "
            "given DAG id(s), failing fast on new import errors."
        ),
    )
    p.add_argument("sources", nargs="+", metavar="PATH", help="DAG file(s) or director(y/ies)")
    p.add_argument("--dest", help="target: s3://bucket/prefix/ or a dags folder path (default: profile dags_folder)")
    p.add_argument("--prefix", default="", help="extra sub-path appended under the target root")
    p.add_argument("--all-files", action="store_true", help="deploy every file, not just *.py / *.zip")
    p.add_argument("--prune", action="store_true", help="delete target files that are not part of this deployment")
    p.add_argument("--dry-run", action="store_true", help="print the plan without changing anything")
    p.add_argument("-y", "--yes", action="store_true", help="do not prompt for confirmation (needed for --prune)")
    p.add_argument("--verify", action="append", metavar="DAG_ID", help="after upload, wait until this DAG is re-parsed (repeatable)")
    p.add_argument("--verify-timeout", type=float, default=120.0, help="max seconds to wait per --verify (default: 120)")
    p.add_argument("--verify-interval", type=float, default=5.0, help="poll interval seconds (default: 5)")
    p.set_defaults(func=cmd_deploy)


def register_undeploy(group: argparse._SubParsersAction) -> None:
    p = group.add_parser(
        "undeploy",
        help="delete DAG file(s) from the dags folder target",
        description=(
            "Delete individual files from the deployment target (s3://... or a local "
            "dags folder). Paths are relative to the target root, exactly as printed "
            "by `dags deploy`. Removing the file makes the DAG stale on the next "
            "parse; use `dags delete <dag_id>` afterwards to also drop its metadata."
        ),
    )
    p.add_argument("paths", nargs="+", metavar="REL_PATH", help="file path(s) relative to the target root, e.g. team_a/old_dag.py")
    p.add_argument("--dest", help="target: s3://bucket/prefix/ or a dags folder path (default: profile dags_folder)")
    p.add_argument("--dry-run", action="store_true", help="print what would be deleted without deleting")
    p.add_argument("-y", "--yes", action="store_true", help="do not prompt for confirmation")
    p.set_defaults(func=cmd_undeploy)


def _normalize_rel(raw: str) -> str:
    path = PurePosixPath(raw.replace("\\", "/"))
    parts = [part for part in path.parts if part != "."]
    if path.is_absolute() or ".." in parts or not parts:
        raise UsageError(
            f"invalid path {raw!r}: must be relative to the target root, without '..'"
        )
    return str(PurePosixPath(*parts))


def cmd_undeploy(ctx: Context, ns) -> int:
    dest = _resolve_dest(ctx, ns)
    rels = list(dict.fromkeys(_normalize_rel(p) for p in ns.paths))
    target = make_target(dest, ctx.settings)

    existing = target.list_keys()
    missing = [rel for rel in rels if rel not in existing]
    if missing:
        # fail before deleting anything, so a typo never causes a partial removal
        raise UsageError(
            f"not found under {target.describe('')}: {', '.join(missing)}"
        )

    if ns.dry_run:
        for rel in rels:
            print(f"[dry-run] would delete {target.describe(rel)}")
        return 0
    for rel in rels:
        print(f"will delete {target.describe(rel)}")
    if not confirm(f"delete {len(rels)} file(s) from the dags folder?", ns.yes):
        print("aborted")
        return 1
    target.delete(rels, log=print)
    print("note: run `datus airflow dags delete <dag_id> -y` to also remove the DAG's metadata")
    return 0


def cmd_deploy(ctx: Context, ns) -> int:
    dest = _resolve_dest(ctx, ns)
    prefix = ns.prefix.strip("/")

    items = collect_files(ns.sources, all_files=ns.all_files)
    if not items:
        raise UsageError("nothing to deploy (directories contained no *.py / *.zip files)")
    if prefix:
        items = [DeployItem(source=item.source, rel=f"{prefix}/{item.rel}") for item in items]

    target = make_target(dest, ctx.settings)
    verify_ids: List[str] = ns.verify or []

    # capture pre-deploy parse markers first, so "re-parsed" means "changed after upload"
    pre_state = {}
    pre_errors = {}
    if verify_ids and not ns.dry_run:
        pre_state = capture_parse_state(ctx.client, verify_ids)
        pre_errors = capture_import_error_state(ctx.client)

    print(f"deploying {len(items)} file(s) to {target.describe('')}")
    if ns.dry_run:
        for item in items:
            print(f"[dry-run] would upload {item.source} -> {target.describe(item.rel)}")
    else:
        target.upload(items, log=print)

    if ns.prune:
        deployed = {item.rel for item in items}
        scope = f"{prefix}/" if prefix else ""
        stale = sorted(
            key
            for key in target.list_keys()
            if key not in deployed and (not scope or key.startswith(scope))
        )
        if not stale:
            print("prune: nothing to remove")
        elif ns.dry_run:
            for key in stale:
                print(f"[dry-run] would delete {target.describe(key)}")
        else:
            for key in stale:
                print(f"prune candidate: {target.describe(key)}")
            if not confirm(f"delete {len(stale)} file(s) not in this deployment?", ns.yes):
                print("prune aborted (files were uploaded, nothing deleted)")
                return 1
            target.delete(stale, log=print)

    if verify_ids and not ns.dry_run:
        print(f"verifying {', '.join(verify_ids)} (timeout {ns.verify_timeout:.0f}s)")
        verify_dags(
            ctx.client,
            verify_ids,
            pre_state,
            [item.rel for item in items],
            pre_errors,
            timeout=ns.verify_timeout,
            interval=ns.verify_interval,
            log=print,
        )
        print("verification succeeded")
    return 0
