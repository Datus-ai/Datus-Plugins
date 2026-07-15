# datus-s3-plugin

A [Datus](https://datus.ai) plugin for browsing and moving **S3** data from
`datus s3 ...` — `ls`/`stat`/`cat`/`head`, `cp`/`sync`/`mv`/`rm`, presigned
URLs, and **S3 Select** (SQL over CSV/JSON/Parquet). Backed by boto3.

```bash
pip install datus-s3-plugin
```

## Configuration

Profiles live under `agent.plugins.s3.<profile>` in Datus' `agent.yml`:

```yaml
agent:
  plugins:
    s3:
      prod:
        default: true
        region: us-east-1
        bucket: my-data-lake        # optional default bucket
        kms_key_id: arn:aws:kms:...:key/abc   # optional SSE-KMS on writes
        endpoint_url: http://minio:9000       # optional (MinIO/S3-compatible)
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Command | Purpose |
|---|---|
| `ls [uri] [-r]` | list objects (or buckets when no URI) |
| `stat <uri>` | object metadata (HEAD) |
| `cat <uri>` / `head <uri>` | print contents / first lines |
| `presign <uri> [--method GET\|PUT]` | presigned URL |
| `select <uri> --sql ... --format csv\|json\|parquet` | S3 Select |
| `cp` / `sync` / `mv` | move objects (local↔S3, S3→S3) |
| `rm <uri> [-r] [-y]` | delete (prompts unless `-y`) |
| `buckets list` / `location <bucket>` | bucket info |

```bash
datus s3 ls s3://my-lake/events/ -r
datus s3 select s3://my-lake/events/day.csv --format csv --header --sql "select count(*) from s3object"
datus s3 cp ./out.parquet s3://my-lake/exports/
datus s3 rm s3://my-lake/tmp/ -r -y
```

## Exit codes

`0` success · `1` runtime/API error · `2` usage (also: `rm` without `-y` when
not interactive) · `3` config error.

## Development

```bash
uv run --package datus-s3-plugin pytest datus-s3-plugin
```

Never imports `datus`; declares the plugin contract in `datus-plugin.yml`
and registers the `s3`
entry point in `datus.plugins`. Shared boto3 plumbing lives in
`datus-aws-common`. Bundled skills: `s3` and `s3-setup`.

## Agent bash permissions

Read/list/preview/`select`/`presign` run everywhere; `cp`/`sync`/`mv` are
routine (confirmed under `normal`, auto under `auto`); `rm` always requires
confirmation. User rules in `agent.yml` always win.
