# datus-aws-common

Shared plumbing for the Datus AWS plugins (`datus-glue-plugin`,
`datus-s3-plugin`, `datus-cloudwatch-plugin`, …). It is **not** a plugin — it
registers no `datus.plugins` entry point — but every AWS plugin depends on it
so the boto3 wiring lives in exactly one place.

Extracted once nine AWS plugins needed the same code (the monorepo's
"copy small helpers until at least three plugins need them" rule points to
extraction well before that).

## What it provides

| Module | Contents |
|---|---|
| `errors` | `PluginError`/`UsageError`/`ConfigError`/`MissingDependencyError`/`ApiError` and exit codes `0/1/2/3/8` (copied from datus-airflow-plugin) |
| `output` | `render_rows` / `render_one` — table / plain / json / yaml (copied) |
| `awsconfig` | `AwsSettings` (region + credentials + AssumeRole + endpoint/timeout) and `validate_keys` for strict per-profile key checking |
| `session` | `build_session` (profile/keys/chain + optional STS AssumeRole) and `build_client` (endpoint override + botocore retry/timeout `Config`) |
| `client` | `call` (botocore `ClientError`/`BotoCoreError` → `ApiError`), `paginate` (boto3 paginators → flat list), `wait_until` (monotonic-deadline poller for `--wait`) |
| `cli` | `AwsContext` (lazy, multi-service client cache), `add_output_option` / `confirm` / `parse_json_arg` / `parse_datetime_arg`, and `run` (parse → dispatch → error-to-exit-code) |

## Credential model

Every AWS plugin profile shares the same credential block; resolution mirrors
the airflow plugin's S3 deploy:

```yaml
region: us-east-1
profile: my-aws-profile                     # or explicit keys / the standard chain
access_key_id: ${AWS_ACCESS_KEY_ID}
secret_access_key: ${AWS_SECRET_ACCESS_KEY}
session_token: ${AWS_SESSION_TOKEN}
role_arn: arn:aws:iam::123456789012:role/datus   # short-lived STS AssumeRole
role_session_name: datus                    # optional
external_id: team-a                         # optional
endpoint_url: http://minio:9000             # optional (S3-compatible stores)
timeout: 60                                 # optional botocore connect/read timeout
max_attempts: 3                             # optional botocore retry attempts
```

## Development

```bash
uv run --package datus-aws-common pytest datus-aws-common
```
