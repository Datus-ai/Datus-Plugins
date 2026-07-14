---
name: s3-setup
description: Configure an environment profile for the `datus s3` plugin (AWS region + credentials, optional default bucket and SSE-KMS key)
---

# S3 Setup

Use this skill when `datus s3` is installed but has no configured environment,
or to add another environment (another account/region, or a MinIO endpoint).

## Config structure

Profiles live under `agent.plugins.s3.<profile>` in the config file named by the
`## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    s3:
      prod:
        default: true
        region: us-east-1

        # credentials — omit to use the standard boto3 chain, otherwise any of:
        profile: my-aws-profile
        access_key_id: ${AWS_ACCESS_KEY_ID}         # secret — env var reference
        secret_access_key: ${AWS_SECRET_ACCESS_KEY} # secret
        role_arn: arn:aws:iam::123456789012:role/datus-s3   # assume this role

        # optional
        bucket: my-data-lake        # default bucket for bare-key arguments
        kms_key_id: arn:aws:kms:us-east-1:123456789012:key/abc   # SSE-KMS on writes
        endpoint_url: http://minio:9000   # S3-compatible stores (MinIO, etc.)
```

## Steps

1. Ask for `region` and the auth method (prefer the AWS chain; use `${VAR}` for
   any keys, never literals). Ask whether they want a default `bucket` and
   whether writes must be SSE-KMS encrypted (`kms_key_id`).
2. The IAM principal needs, at minimum, `s3:ListBucket` + `s3:GetObject` for
   read/select; add `s3:PutObject` for `cp`/`sync`/`mv` and `s3:DeleteObject`
   for `rm`. `presign` needs no extra permission beyond the signed operation.
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with a cheap read-only call: `datus s3 ls` (lists buckets) or
   `datus s3 ls s3://<bucket>/ --limit 5`.

## Troubleshooting

- `no AWS credentials found` / `no AWS region configured` — set credentials or
  `region` (see above).
- `AccessDenied` on read — the principal lacks `s3:GetObject`/`s3:ListBucket`
  on that bucket/prefix.
- MinIO or other S3-compatible store — set `endpoint_url`.
