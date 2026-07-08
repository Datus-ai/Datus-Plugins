"""`datus s3 <ls|stat|cat|head|presign|select|cp|sync|mv|rm>` — object commands."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from datus_aws_common import (
    UsageError,
    add_output_option,
    call,
    confirm,
    render_one,
    render_rows,
)

from ..paths import S3Path, is_s3, parse_s3_uri


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ls", help="list objects (or buckets when no URI is given)")
    p.add_argument("uri", nargs="?", help="s3://bucket/prefix/ (omit to list buckets)")
    p.add_argument("-r", "--recursive", action="store_true", help="recurse into sub-prefixes")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("stat", help="show object metadata (HEAD)")
    p.add_argument("uri")
    add_output_option(p)
    p.set_defaults(func=cmd_stat)

    p = sub.add_parser("cat", help="print an object's contents to stdout")
    p.add_argument("uri")
    p.add_argument("--max-bytes", type=int, help="only fetch the first N bytes")
    p.set_defaults(func=cmd_cat)

    p = sub.add_parser("head", help="print the first lines of an object")
    p.add_argument("uri")
    p.add_argument("-n", "--lines", type=int, default=10)
    p.add_argument("--bytes", type=int, default=65536, help="max bytes to fetch (default 64KiB)")
    p.set_defaults(func=cmd_head)

    p = sub.add_parser("presign", help="generate a presigned URL")
    p.add_argument("uri")
    p.add_argument("--method", choices=["GET", "PUT"], default="GET")
    p.add_argument("--expires", type=int, default=3600, help="seconds until expiry (default 3600)")
    p.set_defaults(func=cmd_presign)

    p = sub.add_parser("select", help="run an S3 Select SQL query over an object")
    p.add_argument("uri")
    p.add_argument("--sql", required=True, help="SQL, e.g. \"select * from s3object s limit 10\"")
    p.add_argument("--format", choices=["csv", "json", "parquet"], default="csv", help="input format")
    p.add_argument("--header", action="store_true", help="CSV has a header row")
    p.add_argument("--json-type", choices=["LINES", "DOCUMENT"], default="LINES")
    p.add_argument("--compression", choices=["NONE", "GZIP", "BZIP2"], help="input compression")
    p.add_argument("--out", choices=["json", "csv"], default="json", help="output format")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("cp", help="copy between local and S3 (or S3 to S3)")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(func=cmd_cp)

    p = sub.add_parser("sync", help="upload a local directory to S3 (new/changed files only)")
    p.add_argument("src")
    p.add_argument("dst")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("mv", help="move (copy then delete source)")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(func=cmd_mv)

    p = sub.add_parser("rm", help="delete object(s)")
    p.add_argument("uri")
    p.add_argument("-r", "--recursive", action="store_true", help="delete all objects under the prefix")
    p.add_argument("-y", "--yes", action="store_true", help="do not prompt for confirmation")
    p.set_defaults(func=cmd_rm)


# ------------------------------------------------------------------ helpers


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else (value or "")


def _default_bucket(ctx):
    return ctx.settings.bucket


def _list_objects(client, path: S3Path):
    """{key: size} for every object under path.key."""
    objs = {}
    token = None
    while True:
        kwargs = {"Bucket": path.bucket, "Prefix": path.key}
        if token:
            kwargs["ContinuationToken"] = token
        resp = call(client.list_objects_v2, **kwargs)
        for c in resp.get("Contents", []):
            objs[c["Key"]] = c.get("Size")
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return objs


def _put(client, bucket, key, data, kms):
    kwargs = {"Bucket": bucket, "Key": key, "Body": data}
    if kms:
        kwargs["ServerSideEncryption"] = "aws:kms"
        kwargs["SSEKMSKeyId"] = kms
    call(client.put_object, **kwargs)


def _copy(client, sb, sk, db, dk, kms):
    kwargs = {"Bucket": db, "Key": dk, "CopySource": {"Bucket": sb, "Key": sk}}
    if kms:
        kwargs["ServerSideEncryption"] = "aws:kms"
        kwargs["SSEKMSKeyId"] = kms
    call(client.copy_object, **kwargs)


def _delete_keys(client, bucket, keys):
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        call(client.delete_objects, Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch]})


# ----------------------------------------------------------------- handlers


def cmd_ls(ctx, ns) -> int:
    client = ctx.client("s3")
    if not ns.uri or ns.uri == "s3://":
        resp = call(client.list_buckets)
        print(render_rows(resp.get("Buckets", []), ["Name", "CreationDate"], ns.output))
        return 0

    path = parse_s3_uri(ns.uri, _default_bucket(ctx))
    kwargs = {"Bucket": path.bucket, "Prefix": path.key}
    if not ns.recursive:
        kwargs["Delimiter"] = "/"
    contents, prefixes = [], []
    token = None
    while True:
        if token:
            kwargs["ContinuationToken"] = token
        resp = call(client.list_objects_v2, **kwargs)
        contents.extend(resp.get("Contents", []))
        prefixes.extend(resp.get("CommonPrefixes", []))
        if not resp.get("IsTruncated") or (ns.limit and len(contents) >= ns.limit):
            break
        token = resp.get("NextContinuationToken")

    if ns.output in ("json", "yaml"):
        print(render_rows(contents, None, ns.output))
        return 0
    rows = [{"Key": p["Prefix"], "Size": "", "LastModified": ""} for p in prefixes]
    picked = contents[: ns.limit] if ns.limit else contents
    rows += [{"Key": c["Key"], "Size": c.get("Size"), "LastModified": _iso(c.get("LastModified"))} for c in picked]
    print(render_rows(rows, ["Key", "Size", "LastModified"], ns.output))
    return 0


def cmd_stat(ctx, ns) -> int:
    client = ctx.client("s3")
    path = parse_s3_uri(ns.uri, _default_bucket(ctx))
    resp = call(client.head_object, Bucket=path.bucket, Key=path.key)
    resp.pop("ResponseMetadata", None)
    print(render_one(resp, ns.output))
    return 0


def cmd_cat(ctx, ns) -> int:
    client = ctx.client("s3")
    path = parse_s3_uri(ns.uri, _default_bucket(ctx))
    kwargs = {"Bucket": path.bucket, "Key": path.key}
    if ns.max_bytes:
        kwargs["Range"] = f"bytes=0-{ns.max_bytes - 1}"
    resp = call(client.get_object, **kwargs)
    sys.stdout.write(resp["Body"].read().decode("utf-8", "replace"))
    return 0


def cmd_head(ctx, ns) -> int:
    client = ctx.client("s3")
    path = parse_s3_uri(ns.uri, _default_bucket(ctx))
    resp = call(client.get_object, Bucket=path.bucket, Key=path.key, Range=f"bytes=0-{ns.bytes - 1}")
    text = resp["Body"].read().decode("utf-8", "replace")
    print("\n".join(text.splitlines()[: ns.lines]))
    return 0


def cmd_presign(ctx, ns) -> int:
    client = ctx.client("s3")
    path = parse_s3_uri(ns.uri, _default_bucket(ctx))
    op = "get_object" if ns.method == "GET" else "put_object"
    url = call(
        client.generate_presigned_url,
        ClientMethod=op,
        Params={"Bucket": path.bucket, "Key": path.key},
        ExpiresIn=ns.expires,
    )
    print(url)
    return 0


def _input_serialization(ns):
    if ns.format == "csv":
        ser = {"CSV": {"FileHeaderInfo": "USE" if ns.header else "NONE"}}
    elif ns.format == "json":
        ser = {"JSON": {"Type": ns.json_type}}
    else:
        ser = {"Parquet": {}}
    if ns.compression and ns.format != "parquet":
        ser["CompressionType"] = ns.compression
    return ser


def cmd_select(ctx, ns) -> int:
    client = ctx.client("s3")
    path = parse_s3_uri(ns.uri, _default_bucket(ctx))
    output_ser = {"JSON": {}} if ns.out == "json" else {"CSV": {}}
    resp = call(
        client.select_object_content,
        Bucket=path.bucket,
        Key=path.key,
        Expression=ns.sql,
        ExpressionType="SQL",
        InputSerialization=_input_serialization(ns),
        OutputSerialization=output_ser,
    )
    chunks = []
    for event in resp.get("Payload", []):
        if "Records" in event:
            chunks.append(event["Records"]["Payload"].decode("utf-8"))
    body = "".join(chunks)
    sys.stdout.write(body if body.endswith("\n") or not body else body + "\n")
    return 0


def cmd_cp(ctx, ns) -> int:
    if is_s3(ns.src) and is_s3(ns.dst):
        return _cp_s3_s3(ctx, ns)
    if is_s3(ns.src):
        return _download(ctx, ns)
    if is_s3(ns.dst):
        return _upload(ctx, ns)
    raise UsageError("at least one of src/dst must be an s3:// URI")


def _upload(ctx, ns) -> int:
    client = ctx.client("s3")
    kms = ctx.settings.kms_key_id
    dpath = parse_s3_uri(ns.dst, _default_bucket(ctx))
    srcp = Path(ns.src).expanduser()
    if ns.recursive:
        if not srcp.is_dir():
            raise UsageError(f"--recursive source must be a directory: {ns.src}")
        base = dpath.key.rstrip("/")
        for f in sorted(p for p in srcp.rglob("*") if p.is_file()):
            rel = f.relative_to(srcp).as_posix()
            key = f"{base}/{rel}" if base else rel
            _put(client, dpath.bucket, key, f.read_bytes(), kms)
            print(f"upload {f} -> s3://{dpath.bucket}/{key}")
        return 0
    if not srcp.is_file():
        raise UsageError(f"source not found: {ns.src}")
    key = f"{dpath.key}{srcp.name}" if dpath.is_prefix() else dpath.key
    _put(client, dpath.bucket, key, srcp.read_bytes(), kms)
    print(f"upload {srcp} -> s3://{dpath.bucket}/{key}")
    return 0


def _download(ctx, ns) -> int:
    client = ctx.client("s3")
    spath = parse_s3_uri(ns.src, _default_bucket(ctx))
    dstp = Path(ns.dst).expanduser()
    if ns.recursive:
        for key in sorted(_list_objects(client, spath)):
            rel = key[len(spath.key):].lstrip("/") if spath.key else key
            target = dstp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            resp = call(client.get_object, Bucket=spath.bucket, Key=key)
            target.write_bytes(resp["Body"].read())
            print(f"download s3://{spath.bucket}/{key} -> {target}")
        return 0
    resp = call(client.get_object, Bucket=spath.bucket, Key=spath.key)
    if dstp.is_dir():
        dstp = dstp / Path(spath.key).name
    dstp.write_bytes(resp["Body"].read())
    print(f"download {spath.uri} -> {dstp}")
    return 0


def _cp_s3_s3(ctx, ns) -> int:
    client = ctx.client("s3")
    kms = ctx.settings.kms_key_id
    spath = parse_s3_uri(ns.src, _default_bucket(ctx))
    dpath = parse_s3_uri(ns.dst, _default_bucket(ctx))
    if ns.recursive:
        for key in sorted(_list_objects(client, spath)):
            rel = key[len(spath.key):].lstrip("/") if spath.key else key
            dkey = f"{dpath.key.rstrip('/')}/{rel}" if dpath.key else rel
            _copy(client, spath.bucket, key, dpath.bucket, dkey, kms)
            print(f"copy s3://{spath.bucket}/{key} -> s3://{dpath.bucket}/{dkey}")
        return 0
    dkey = f"{dpath.key}{Path(spath.key).name}" if dpath.is_prefix() else dpath.key
    _copy(client, spath.bucket, spath.key, dpath.bucket, dkey, kms)
    print(f"copy {spath.uri} -> s3://{dpath.bucket}/{dkey}")
    return 0


def cmd_sync(ctx, ns) -> int:
    client = ctx.client("s3")
    kms = ctx.settings.kms_key_id
    srcp = Path(ns.src).expanduser()
    if not srcp.is_dir():
        raise UsageError("sync source must be a local directory")
    dpath = parse_s3_uri(ns.dst, _default_bucket(ctx))
    base = dpath.key.rstrip("/")
    remote = _list_objects(client, dpath)
    uploaded = 0
    for f in sorted(p for p in srcp.rglob("*") if p.is_file()):
        rel = f.relative_to(srcp).as_posix()
        key = f"{base}/{rel}" if base else rel
        if remote.get(key) == f.stat().st_size:
            continue
        _put(client, dpath.bucket, key, f.read_bytes(), kms)
        print(f"upload {f} -> s3://{dpath.bucket}/{key}")
        uploaded += 1
    print(f"synced {uploaded} file(s)")
    return 0


def cmd_mv(ctx, ns) -> int:
    rc = cmd_cp(ctx, ns)
    if rc:
        return rc
    client = ctx.client("s3")
    if is_s3(ns.src):
        spath = parse_s3_uri(ns.src, _default_bucket(ctx))
        if ns.recursive:
            _delete_keys(client, spath.bucket, list(_list_objects(client, spath)))
        else:
            call(client.delete_object, Bucket=spath.bucket, Key=spath.key)
    else:
        p = Path(ns.src).expanduser()
        if ns.recursive and p.is_dir():
            shutil.rmtree(p)
        elif p.is_file():
            p.unlink()
    print(f"moved {ns.src} -> {ns.dst}")
    return 0


def cmd_rm(ctx, ns) -> int:
    client = ctx.client("s3")
    path = parse_s3_uri(ns.uri, _default_bucket(ctx))
    if ns.recursive:
        keys = list(_list_objects(client, path))
        if not keys:
            print("nothing to delete")
            return 0
        if not confirm(f"delete {len(keys)} object(s) under {path.uri}?", ns.yes):
            print("aborted")
            return 1
        _delete_keys(client, path.bucket, keys)
        print(f"deleted {len(keys)} object(s)")
        return 0
    if not confirm(f"delete {path.uri}?", ns.yes):
        print("aborted")
        return 1
    call(client.delete_object, Bucket=path.bucket, Key=path.key)
    print(f"deleted {path.uri}")
    return 0
