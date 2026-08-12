from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import Any

from datus_azure_common import (
    UsageError,
    add_output_option,
    call,
    render_one,
    render_rows,
    run,
)

from .client import AdlsContext
from .config import Settings
from .paths import AdlsPath, is_adls, parse_adls_uri

SPOOL_MAX_BYTES = 32 * 1024 * 1024


def _confirm(prompt: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise UsageError(f"{prompt}; pass -y/--yes")
    if input(f"{prompt} [y/N] ").strip().lower() not in {"y", "yes"}:
        raise UsageError("cancelled")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datus adls", description="Browse Azure Blob and ADLS Gen2 data."
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
    p = sub.add_parser("sas")
    p.add_argument("uri")
    p.add_argument("--permissions", default="r")
    p.add_argument("--expires", type=int, default=3600)
    p.set_defaults(func=cmd_sas)
    group = sub.add_parser("filesystems").add_subparsers(
        dest="subcommand", required=True
    )
    p = group.add_parser("list")
    add_output_option(p)
    p.set_defaults(func=cmd_filesystems_list)
    group = sub.add_parser("acl").add_subparsers(dest="subcommand", required=True)
    p = group.add_parser("get")
    p.add_argument("uri")
    add_output_option(p)
    p.set_defaults(func=cmd_acl_get)
    p = group.add_parser("set")
    p.add_argument("uri")
    p.add_argument("--acl", required=True)
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_acl_set)
    return parser


def _path(ctx, raw):
    return parse_adls_uri(raw, ctx.settings.container)


def _file(ctx, path: AdlsPath):
    return ctx.filesystem(path.filesystem).get_file_client(path.key)


def _props(value) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return (
        {key: val for key, val in vars(value).items() if not key.startswith("_")}
        if hasattr(value, "__dict__")
        else {"value": value}
    )


def _paths(ctx, path: AdlsPath, recursive=True):
    return list(
        call(
            ctx.filesystem(path.filesystem).get_paths,
            path=path.key or None,
            recursive=recursive,
        )
    )


def cmd_ls(ctx, ns):
    if not ns.uri:
        return cmd_filesystems_list(ctx, ns)
    path = _path(ctx, ns.uri)
    rows = []
    for item in _paths(ctx, path, ns.recursive):
        data = _props(item)
        rows.append(
            {
                "name": data.get("name"),
                "is_directory": data.get("is_directory"),
                "content_length": data.get("content_length"),
                "last_modified": data.get("last_modified"),
            }
        )
        if ns.limit and len(rows) >= ns.limit:
            break
    print(
        render_rows(
            rows, ["name", "is_directory", "content_length", "last_modified"], ns.output
        )
    )
    return 0


def cmd_stat(ctx, ns):
    print(
        render_one(call(_file(ctx, _path(ctx, ns.uri)).get_file_properties), ns.output)
    )
    return 0


def _download(ctx, path, offset=None, length=None):
    """Read a bounded range into memory. Only for `cat` and `head` previews."""
    stream = call(_file(ctx, path).download_file, offset=offset, length=length)
    return call(stream.readall)


def _download_into(ctx, path: AdlsPath, sink) -> None:
    stream = call(_file(ctx, path).download_file)
    call(stream.readinto, sink)


def _local_target(dst: Path, relative: str) -> Path:
    """Resolve a remote-derived relative name below `dst`, or refuse it.

    Blob names may contain any character combination, including `..` segments
    that would otherwise escape the destination directory.
    """
    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise UsageError(f"refusing to write outside {dst}: {relative}")
    root = dst.resolve()
    target = root.joinpath(*candidate.parts).resolve()
    if target != root and root not in target.parents:
        raise UsageError(f"refusing to write outside {dst}: {relative}")
    return target


def cmd_cat(ctx, ns):
    data = _download(ctx, _path(ctx, ns.uri), length=ns.max_bytes)
    sys.stdout.write(data.decode("utf-8", "replace"))
    return 0


def cmd_head(ctx, ns):
    data = _download(ctx, _path(ctx, ns.uri), length=ns.bytes)
    print("\n".join(data.decode("utf-8", "replace").splitlines()[: ns.lines]))
    return 0


def _ensure_dirs(ctx, path: AdlsPath):
    fs = ctx.filesystem(path.filesystem)
    parts = path.key.split("/")[:-1]
    for idx in range(1, len(parts) + 1):
        directory = fs.get_directory_client("/".join(parts[:idx]))
        try:
            directory.create_directory()
        except Exception as exc:
            if "exist" not in str(exc).lower() and "409" not in str(exc):
                raise


def _upload(ctx, source: Path, dst: AdlsPath):
    key = f"{dst.key}{source.name}" if dst.is_prefix() else dst.key
    target = AdlsPath(dst.filesystem, key)
    _ensure_dirs(ctx, target)
    with source.open("rb") as handle:
        call(
            _file(ctx, target).upload_data,
            handle,
            length=source.stat().st_size,
            overwrite=True,
        )
    print(f"upload {source} -> {target.uri}")


def _copy_remote(ctx, src: AdlsPath, dst: AdlsPath):
    key = f"{dst.key}{Path(src.key).name}" if dst.is_prefix() else dst.key
    target = AdlsPath(dst.filesystem, key)
    _ensure_dirs(ctx, target)
    with SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES) as buffer:
        _download_into(ctx, src, buffer)
        length = buffer.tell()
        buffer.seek(0)
        call(_file(ctx, target).upload_data, buffer, length=length, overwrite=True)
    print(f"copy {src.uri} -> {target.uri}")


def cmd_cp(ctx, ns):
    if is_adls(ns.src) and is_adls(ns.dst):
        src, dst = _path(ctx, ns.src), _path(ctx, ns.dst)
        if ns.recursive:
            for item in _paths(ctx, src):
                data = _props(item)
                if data.get("is_directory"):
                    continue
                name = str(data.get("name"))
                rel = name[len(src.key) :].lstrip("/")
                _copy_remote(
                    ctx,
                    AdlsPath(src.filesystem, name),
                    AdlsPath(
                        dst.filesystem, f"{dst.key.rstrip('/')}/{rel}".lstrip("/")
                    ),
                )
        else:
            _copy_remote(ctx, src, dst)
        return 0
    if is_adls(ns.dst):
        src, dst = Path(ns.src).expanduser(), _path(ctx, ns.dst)
        if ns.recursive:
            if not src.is_dir():
                raise UsageError("recursive source must be a directory")
            for file in sorted(p for p in src.rglob("*") if p.is_file()):
                _upload(
                    ctx,
                    file,
                    AdlsPath(
                        dst.filesystem,
                        f"{dst.key.rstrip('/')}/{file.relative_to(src).as_posix()}".lstrip(
                            "/"
                        ),
                    ),
                )
        else:
            _upload(ctx, src, dst)
        return 0
    if is_adls(ns.src):
        src, dst = _path(ctx, ns.src), Path(ns.dst).expanduser()
        entries = _paths(ctx, src) if ns.recursive else [None]
        for item in entries:
            if item is not None and _props(item).get("is_directory"):
                continue
            name = str(_props(item).get("name")) if item is not None else src.key
            remote = AdlsPath(src.filesystem, name)
            target = (
                _local_target(dst, name[len(src.key) :].lstrip("/"))
                if ns.recursive
                else (dst / Path(src.key).name if dst.is_dir() else dst)
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                _download_into(ctx, remote, handle)
            print(f"download {remote.uri} -> {target}")
        return 0
    raise UsageError("at least one of src/dst must be an abfss:// URI")


def cmd_sync(ctx, ns):
    src, dst = Path(ns.src).expanduser(), _path(ctx, ns.dst)
    if not src.is_dir():
        raise UsageError("sync source must be a directory")
    remote = {}
    for item in _paths(ctx, dst):
        data = _props(item)
        if not data.get("is_directory"):
            remote[str(data.get("name"))] = data.get("content_length")
    count = 0
    for file in sorted(p for p in src.rglob("*") if p.is_file()):
        key = f"{dst.key.rstrip('/')}/{file.relative_to(src).as_posix()}".lstrip("/")
        if remote.get(key) == file.stat().st_size:
            continue
        _upload(ctx, file, AdlsPath(dst.filesystem, key))
        count += 1
    print(f"synced {count} file(s)")
    return 0


def cmd_mv(ctx, ns):
    rc = cmd_cp(ctx, ns)
    if is_adls(ns.src):
        cmd_rm(ctx, argparse.Namespace(uri=ns.src, recursive=ns.recursive, yes=True))
    elif Path(ns.src).is_dir() and ns.recursive:
        import shutil

        shutil.rmtree(ns.src)
    else:
        Path(ns.src).unlink()
    return rc


def cmd_rm(ctx, ns):
    path = _path(ctx, ns.uri)
    _confirm(f"delete {path.uri}{' recursively' if ns.recursive else ''}", ns.yes)
    if ns.recursive:
        call(
            ctx.filesystem(path.filesystem)
            .get_directory_client(path.key.rstrip("/"))
            .delete_directory
        )
    else:
        call(_file(ctx, path).delete_file)
    print(f"delete {path.uri}")
    return 0


def cmd_sas(ctx, ns):
    if ctx.settings.sas_token and not ctx.settings.account_key:
        raise UsageError(
            "sas_token profiles cannot mint a SAS; a user delegation key requires a "
            "Microsoft Entra credential with generateUserDelegationKey permission, "
            "so configure account_key or an Entra identity instead"
        )
    try:
        from azure.storage.blob import (
            BlobSasPermissions,
            BlobServiceClient,
            generate_blob_sas,
        )
    except ImportError as exc:
        raise UsageError("azure-storage-blob is required for SAS generation") from exc
    path = _path(ctx, ns.uri)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=ns.expires)
    permission = BlobSasPermissions.from_string(ns.permissions)
    kwargs = {
        "account_name": ctx.settings.account_name,
        "container_name": path.filesystem,
        "blob_name": path.key,
        "permission": permission,
        "expiry": expiry,
    }
    if ctx.settings.account_key:
        kwargs["account_key"] = ctx.settings.account_key
    else:
        service = BlobServiceClient(ctx.settings.blob_url, credential=ctx.credential)
        kwargs["user_delegation_key"] = call(
            service.get_user_delegation_key, datetime.now(timezone.utc), expiry
        )
    token = generate_blob_sas(**kwargs)
    print(f"{ctx.settings.account_url}/{path.filesystem}/{path.key}?{token}")
    return 0


def cmd_filesystems_list(ctx, ns):
    rows = [_props(item) for item in call(ctx.client.list_file_systems)]
    print(render_rows(rows, ["name", "last_modified", "public_access"], ns.output))
    return 0


def cmd_acl_get(ctx, ns):
    path = _path(ctx, ns.uri)
    target = (
        ctx.filesystem(path.filesystem).get_directory_client(path.key)
        if path.is_prefix()
        else _file(ctx, path)
    )
    print(render_one(call(target.get_access_control), ns.output))
    return 0


def cmd_acl_set(ctx, ns):
    path = _path(ctx, ns.uri)
    _confirm(f"replace ACL on {path.uri}", ns.yes)
    target = (
        ctx.filesystem(path.filesystem).get_directory_client(path.key)
        if path.is_prefix()
        else _file(ctx, path)
    )
    call(target.set_access_control, acl=ns.acl)
    print(f"updated ACL on {path.uri}")
    return 0


def main(argv: list[str], profile: dict[str, Any]) -> int:
    return run(
        build_parser(), argv, lambda: AdlsContext(Settings.from_profile(profile))
    )
