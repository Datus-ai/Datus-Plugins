from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from datus_gcp_common import (
    UsageError,
    add_output_option,
    call,
    render_one,
    render_rows,
    run,
)

from .client import GcsContext
from .config import Settings
from .paths import GcsPath, is_gcs, parse_gcs_uri


def _confirm(prompt: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise UsageError(f"{prompt}; pass -y/--yes")
    if input(f"{prompt} [y/N] ").strip().lower() not in {"y", "yes"}:
        raise UsageError("cancelled")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datus gcs", description="Browse and move Google Cloud Storage data."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ls")
    p.add_argument("uri", nargs="?")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_ls)
    p = sub.add_parser("stat")
    p.add_argument("uri")
    add_output_option(p)
    p.set_defaults(func=cmd_stat)
    p = sub.add_parser("cat")
    p.add_argument("uri")
    p.add_argument("--max-bytes", type=int)
    p.set_defaults(func=cmd_cat)
    p = sub.add_parser("head")
    p.add_argument("uri")
    p.add_argument("-n", "--lines", type=int, default=10)
    p.add_argument("--bytes", type=int, default=65536)
    p.set_defaults(func=cmd_head)
    p = sub.add_parser("cp")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(func=cmd_cp)
    p = sub.add_parser("sync")
    p.add_argument("src")
    p.add_argument("dst")
    p.set_defaults(func=cmd_sync)
    p = sub.add_parser("mv")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(func=cmd_mv)
    p = sub.add_parser("rm")
    p.add_argument("uri")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_rm)
    p = sub.add_parser("signurl")
    p.add_argument("uri")
    p.add_argument("--method", choices=["GET", "PUT"], default="GET")
    p.add_argument("--expires", type=int, default=3600)
    p.set_defaults(func=cmd_signurl)
    group = sub.add_parser("buckets").add_subparsers(dest="subcommand", required=True)
    p = group.add_parser("list")
    add_output_option(p)
    p.set_defaults(func=cmd_buckets_list)
    p = group.add_parser("location")
    p.add_argument("bucket")
    add_output_option(p)
    p.set_defaults(func=cmd_buckets_location)
    group = sub.add_parser("lifecycle").add_subparsers(dest="subcommand", required=True)
    p = group.add_parser("get")
    p.add_argument("bucket", nargs="?")
    add_output_option(p)
    p.set_defaults(func=cmd_lifecycle_get)
    p = group.add_parser("set")
    p.add_argument("--file", required=True)
    p.add_argument("bucket", nargs="?")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_lifecycle_set)
    return parser


def _path(ctx, raw: str) -> GcsPath:
    return parse_gcs_uri(raw, ctx.settings.bucket)


def _blob_dict(blob) -> dict[str, Any]:
    return {
        "name": blob.name,
        "bucket": blob.bucket.name,
        "size": blob.size,
        "updated": blob.updated,
        "content_type": blob.content_type,
        "generation": blob.generation,
    }


def _list(ctx, path: GcsPath):
    return list(call(ctx.client.list_blobs, path.bucket, prefix=path.key))


def cmd_ls(ctx, ns):
    if not ns.uri or ns.uri == "gs://":
        rows = [
            {"name": b.name, "location": b.location, "storage_class": b.storage_class}
            for b in call(ctx.client.list_buckets)
        ]
    else:
        path = _path(ctx, ns.uri)
        delimiter = None if ns.recursive else "/"
        iterator = call(
            ctx.client.list_blobs, path.bucket, prefix=path.key, delimiter=delimiter
        )
        rows = [_blob_dict(blob) for blob in iterator]
        prefixes = getattr(iterator, "prefixes", set())
        rows = [
            {"name": prefix, "bucket": path.bucket, "size": ""}
            for prefix in sorted(prefixes)
        ] + rows
    if ns.limit:
        rows = rows[: ns.limit]
    print(render_rows(rows, ["name", "size", "updated", "storage_class"], ns.output))
    return 0


def cmd_stat(ctx, ns):
    path = _path(ctx, ns.uri)
    blob = ctx.client.bucket(path.bucket).blob(path.key)
    call(blob.reload)
    print(render_one(_blob_dict(blob), ns.output))
    return 0


def cmd_cat(ctx, ns):
    path = _path(ctx, ns.uri)
    end = ns.max_bytes - 1 if ns.max_bytes else None
    data = call(
        ctx.client.bucket(path.bucket).blob(path.key).download_as_bytes, end=end
    )
    sys.stdout.write(data.decode("utf-8", "replace"))
    return 0


def cmd_head(ctx, ns):
    path = _path(ctx, ns.uri)
    data = call(
        ctx.client.bucket(path.bucket).blob(path.key).download_as_bytes,
        end=ns.bytes - 1,
    )
    print("\n".join(data.decode("utf-8", "replace").splitlines()[: ns.lines]))
    return 0


def _upload(ctx, source: Path, dst: GcsPath):
    key = f"{dst.key}{source.name}" if dst.is_prefix() else dst.key
    call(ctx.client.bucket(dst.bucket).blob(key).upload_from_filename, str(source))
    print(f"upload {source} -> gs://{dst.bucket}/{key}")


def _copy_one(ctx, src: GcsPath, dst: GcsPath):
    source_bucket = ctx.client.bucket(src.bucket)
    source_blob = source_bucket.blob(src.key)
    key = f"{dst.key}{Path(src.key).name}" if dst.is_prefix() else dst.key
    call(source_bucket.copy_blob, source_blob, ctx.client.bucket(dst.bucket), key)
    print(f"copy {src.uri} -> gs://{dst.bucket}/{key}")


def cmd_cp(ctx, ns):
    if is_gcs(ns.src) and is_gcs(ns.dst):
        src, dst = _path(ctx, ns.src), _path(ctx, ns.dst)
        if ns.recursive:
            for blob in _list(ctx, src):
                rel = blob.name[len(src.key) :].lstrip("/")
                _copy_one(
                    ctx,
                    GcsPath(src.bucket, blob.name),
                    GcsPath(dst.bucket, f"{dst.key.rstrip('/')}/{rel}".lstrip("/")),
                )
        else:
            _copy_one(ctx, src, dst)
        return 0
    if is_gcs(ns.dst):
        src, dst = Path(ns.src).expanduser(), _path(ctx, ns.dst)
        if ns.recursive:
            if not src.is_dir():
                raise UsageError("recursive source must be a directory")
            for file in sorted(p for p in src.rglob("*") if p.is_file()):
                rel = file.relative_to(src).as_posix()
                _upload(
                    ctx,
                    file,
                    GcsPath(dst.bucket, f"{dst.key.rstrip('/')}/{rel}".lstrip("/")),
                )
        else:
            if not src.is_file():
                raise UsageError(f"source not found: {src}")
            _upload(ctx, src, dst)
        return 0
    if is_gcs(ns.src):
        src, dst = _path(ctx, ns.src), Path(ns.dst).expanduser()
        blobs = (
            _list(ctx, src)
            if ns.recursive
            else [ctx.client.bucket(src.bucket).blob(src.key)]
        )
        for blob in blobs:
            target = (
                dst / blob.name[len(src.key) :].lstrip("/")
                if ns.recursive
                else (dst / Path(src.key).name if dst.is_dir() else dst)
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            call(blob.download_to_filename, str(target))
            print(f"download gs://{src.bucket}/{blob.name} -> {target}")
        return 0
    raise UsageError("at least one of src/dst must be a gs:// URI")


def cmd_sync(ctx, ns):
    src = Path(ns.src).expanduser()
    dst = _path(ctx, ns.dst)
    if not src.is_dir():
        raise UsageError("sync source must be a directory")
    remote = {blob.name: blob.size for blob in _list(ctx, dst)}
    uploaded = 0
    for file in sorted(p for p in src.rglob("*") if p.is_file()):
        key = f"{dst.key.rstrip('/')}/{file.relative_to(src).as_posix()}".lstrip("/")
        if remote.get(key) == file.stat().st_size:
            continue
        call(ctx.client.bucket(dst.bucket).blob(key).upload_from_filename, str(file))
        uploaded += 1
    print(f"synced {uploaded} file(s)")
    return 0


def cmd_mv(ctx, ns):
    rc = cmd_cp(ctx, ns)
    if is_gcs(ns.src):
        shadow = argparse.Namespace(uri=ns.src, recursive=ns.recursive, yes=True)
        cmd_rm(ctx, shadow)
    elif Path(ns.src).is_dir() and ns.recursive:
        import shutil

        shutil.rmtree(ns.src)
    else:
        Path(ns.src).unlink()
    return rc


def cmd_rm(ctx, ns):
    path = _path(ctx, ns.uri)
    _confirm(f"delete {path.uri}{' recursively' if ns.recursive else ''}", ns.yes)
    blobs = (
        _list(ctx, path)
        if ns.recursive
        else [ctx.client.bucket(path.bucket).blob(path.key)]
    )
    for blob in blobs:
        call(blob.delete)
        print(f"delete gs://{path.bucket}/{blob.name}")
    return 0


def cmd_signurl(ctx, ns):
    path = _path(ctx, ns.uri)
    url = call(
        ctx.client.bucket(path.bucket).blob(path.key).generate_signed_url,
        version="v4",
        expiration=ns.expires,
        method=ns.method,
    )
    print(url)
    return 0


def cmd_buckets_list(ctx, ns):
    rows = [
        {"name": b.name, "location": b.location, "storage_class": b.storage_class}
        for b in call(ctx.client.list_buckets)
    ]
    print(render_rows(rows, ["name", "location", "storage_class"], ns.output))
    return 0


def cmd_buckets_location(ctx, ns):
    bucket = call(ctx.client.get_bucket, ns.bucket)
    print(render_one({"bucket": bucket.name, "location": bucket.location}, ns.output))
    return 0


def _bucket(ctx, name):
    value = name or ctx.settings.bucket
    if not value:
        raise UsageError("bucket is required")
    return ctx.client.bucket(value)


def cmd_lifecycle_get(ctx, ns):
    bucket = _bucket(ctx, ns.bucket)
    call(bucket.reload)
    print(
        render_one(
            {"bucket": bucket.name, "lifecycle_rules": list(bucket.lifecycle_rules)},
            ns.output,
        )
    )
    return 0


def cmd_lifecycle_set(ctx, ns):
    _confirm("replace bucket lifecycle policy", ns.yes)
    raw = Path(ns.file).read_text(encoding="utf-8")
    try:
        rules = json.loads(raw)
    except ValueError:
        import yaml

        rules = yaml.safe_load(raw)
    if not isinstance(rules, list):
        raise UsageError("lifecycle file must contain a list of rules")
    bucket = _bucket(ctx, ns.bucket)
    bucket.lifecycle_rules = rules
    call(bucket.patch)
    print(f"updated lifecycle for {bucket.name}")
    return 0


def main(argv: list[str], profile: dict[str, Any]) -> int:
    return run(build_parser(), argv, lambda: GcsContext(Settings.from_profile(profile)))
