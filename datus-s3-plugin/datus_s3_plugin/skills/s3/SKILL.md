---
name: s3
description: Browse and move S3 data (ls/stat/cat/head/cp/sync/mv/rm/presign) and run S3 Select SQL over CSV/JSON/Parquet objects via the `datus s3` CLI
---

# S3

`datus s3` browses and moves S3 data through boto3. Global usage:

```
datus s3 [--profile <env>] <command> [args...]
```

Object arguments are `s3://bucket/key` URIs (or a bare key if a default
`bucket` is configured). Add `-o json` to list/read commands for full output.

## Browse & read

```
datus s3 ls [s3://bucket/prefix/] [-r] [--limit N]     # no URI -> list buckets
datus s3 stat s3://bucket/key                           # object metadata (HEAD)
datus s3 cat s3://bucket/key [--max-bytes N]            # contents to stdout
datus s3 head s3://bucket/key [-n 10] [--bytes 65536]   # first lines
datus s3 presign s3://bucket/key [--method GET|PUT] [--expires 3600]
datus s3 buckets list | location <bucket>
```

`presign --method PUT` returns a URL that grants **write** access to that key —
treat it like a credential.

## S3 Select

Query a single object with SQL without downloading it:

```
datus s3 select s3://bucket/data.csv --format csv --header \
    --sql "select s.region, count(*) from s3object s group by s.region"
datus s3 select s3://bucket/data.json --format json --json-type LINES --sql "select * from s3object[*] s limit 5"
datus s3 select s3://bucket/data.parquet --format parquet --sql "select * from s3object limit 5"
```

`--compression GZIP|BZIP2` for compressed CSV/JSON; `--out json|csv` picks the
output shape.

## Move data

```
datus s3 cp <src> <dst> [-r]      # local<->s3 or s3->s3
datus s3 sync <local-dir> s3://bucket/prefix/   # upload new/changed files only
datus s3 mv <src> <dst> [-r]      # copy then delete source
datus s3 rm s3://bucket/key [-r] [-y]   # delete; prompts unless -y
```

- `cp`/`sync`/`mv` write objects; `rm` deletes and prompts for confirmation
  (pass `-y` when scripting). `rm -r` deletes every object under a prefix — be
  careful, and prefer `ls -r` first to see what will go.
- Writes use SSE-KMS when `kms_key_id` is set in the profile.

## Exit codes

`0` success · `1` runtime/API error · `2` usage (also: `rm` without `-y` when
not interactive) · `3` config error.
