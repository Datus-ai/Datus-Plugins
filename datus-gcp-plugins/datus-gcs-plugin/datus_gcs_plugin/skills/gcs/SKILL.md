---
name: gcs
description: Browse, inspect, preview, upload, download, copy, sync, move, delete, and sign Google Cloud Storage objects, and inspect buckets or replace lifecycle rules with datus gcs. Use for gs:// data movement, artifact publishing, signed URLs, or GCS lifecycle management.
---

# GCS

Use `datus gcs` for Google Cloud Storage objects and buckets.

```bash
datus gcs [--profile <env>] <command> [args...]
```

Object arguments use `gs://bucket/key`. A bare key uses the profile's default
`bucket`; without one, supply a full URI. A trailing `/` marks a destination as
a prefix. Commands with `-o` accept `table|json|yaml|plain`; prefer JSON for
exact metadata.

`cp` and `mv` are the exception: they treat every operand without a `gs://`
prefix as a local path, so each remote operand must be written as an explicit
`gs://` URI even when the profile sets a default `bucket`.

## Command catalogue

Browse and read:

```bash
datus gcs ls [uri] [-r|--recursive] [--limit N] [-o json]
datus gcs stat <uri> [-o json]
datus gcs cat <uri> [--max-bytes N]
datus gcs head <uri> [-n|--lines N] [--bytes N]
datus gcs buckets list [-o json]
datus gcs buckets location <bucket> [-o json]
datus gcs lifecycle get [bucket] [-o json]
```

`ls` without a URI (or with `gs://`) lists buckets. Non-recursive object
listing groups prefixes; `-r` walks all matching objects. `cat` writes decoded
text to stdout, while `head` downloads at most 65536 bytes by default and shows
10 lines. Use `stat` before overwriting or deleting an object.

Move and publish data:

```bash
datus gcs cp <src> <dst> [-r|--recursive]
datus gcs sync <local-dir> <gs://bucket/prefix/>
datus gcs mv <src> <dst> [-r|--recursive]
datus gcs rm <uri> [-r|--recursive] [-y|--yes]
```

- `cp` supports local-to-GCS, GCS-to-local, and GCS-to-GCS. At least one side
  must be `gs://`. It overwrites the destination object when names collide.
- `sync` only uploads local files whose remote object size differs; it does not
  download or delete remote-only objects and is not a checksum comparison.
- `mv` copies first and then deletes the source. Treat it as destructive.
- `rm` requires confirmation; in non-interactive use pass `-y`. `rm -r`
  deletes every object under the prefix, so run `ls -r` first.

Signed URLs and lifecycle:

```bash
datus gcs signurl <uri> [--method GET|PUT] [--expires 3600]
datus gcs lifecycle set [bucket] --file <rules.json|yaml> [-y|--yes]
```

A signed URL is a credential until expiry. `PUT` grants write access to the
exact key; never expose it in logs or chat. URL signing also requires the active
credential type to support signing. `lifecycle set` expects a list of rules and
replaces the bucket's complete lifecycle policy; always run `lifecycle get`
first, preserve wanted rules, review the file, and confirm.

## Permission posture and workflow

Reads (`ls`, `stat`, `cat`, `head`, bucket inspection, lifecycle get) are
allowed directly. In normal mode, writes (`cp`, `sync`, `mv`), deletion,
signing, and lifecycle replacement require approval. Auto mode permits data
writes/moves but still asks for `rm`, `signurl`, and `lifecycle set`.

Publish and verify artifacts with one sync plus read-back:

```bash
datus gcs --profile prod sync ./artifacts/ gs://builds/app/v1.4.0/
datus gcs --profile prod ls gs://builds/app/v1.4.0/ -r -o json
datus gcs --profile prod stat gs://builds/app/v1.4.0/app.tar.gz -o json
```

Grant only the GCS IAM roles/permissions required: object viewer for reads,
object creator/admin for writes or deletes, bucket metadata access for bucket
inspection, and bucket update permission for lifecycle changes.

## Exit codes

`0` success · `1` runtime/API error · `2` usage/cancelled confirmation ·
`3` config error · `8` missing dependency · `130` interrupted.
