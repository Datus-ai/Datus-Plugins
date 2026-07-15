# datus-airflow-plugin

A [Datus](https://datus.ai) plugin that drives **remote Apache Airflow 3.x**
deployments from `datus airflow ...`, backed entirely by the
[Airflow REST API v2](https://airflow.apache.org/docs/apache-airflow/3.1.6/stable-rest-api-ref.html)
— no Airflow installation needed on the client. Command groups mirror the
Airflow CLI, plus a `dags deploy` command that ships DAG files to **S3** or a
local/mounted dags folder and verifies the scheduler picked them up.

```bash
pip install datus-airflow-plugin            # requests + PyYAML + boto3 (S3 deploy included)
```

## Configuration

Profiles live under `agent.plugins.airflow.<profile>` in Datus' `agent.yml`
(`./conf/agent.yml` or `~/.datus/conf/agent.yml`):

```yaml
agent:
  plugins:
    airflow:
      prod:
        default: true
        api_base_url: https://airflow.example.com   # server root, without /api/v2
        username: admin
        password: ${AIRFLOW_PASSWORD}               # or a static JWT: token: ${AIRFLOW_API_TOKEN}
        dags_folder: s3://my-bucket/dags/           # default `dags deploy` target
        s3:                                         # optional S3 overrides
          region: us-east-1
          # profile: my-aws-profile / endpoint_url: http://minio:9000
          # access_key_id: ${AWS_ACCESS_KEY_ID} / secret_access_key: ${AWS_SECRET_ACCESS_KEY}
      staging:
        api_base_url: http://localhost:8080
        username: admin
        password: ${AIRFLOW_STAGING_PASSWORD}
        dags_folder: /opt/airflow/dags
```

Select an environment with `datus airflow --profile staging ...`; the
`default: true` profile is used otherwise.

Authentication follows the Airflow 3 model: username/password are exchanged
for a JWT at `POST /auth/token` (SimpleAuthManager and FabAuthManager both
expose it; override the URL with `auth_token_url` if needed). Tokens are
cached under `~/.cache/datus-airflow-plugin/` (0600) and refreshed on expiry;
set `cache_token: false` to disable. Self-signed TLS: set `verify_ssl` to a CA
bundle path (or `false`).

## Commands

Everything accepts `-o table|json|yaml|plain` where output is structured
(json/yaml emit the full API objects). Destructive commands prompt — pass
`-y/--yes` in scripts.

| Group | Subcommands |
|---|---|
| `dags` | `list`, `details`, `list-runs`, `list-import-errors`, `show` (ASCII task tree), `source`, `pause`, `unpause`, `trigger [--wait]`, `state`, `clear-run`, `delete`, `next-execution`, **`deploy`**, **`undeploy`** |
| `tasks` | `list`, `state`, `states-for-dag-run`, `clear`, `failed-deps`, `logs` |
| `variables` | `list`, `get`, `set`, `delete`, `import`, `export` |
| `connections` | `list`, `get`, `add`, `delete`, `test`, `import`, `export` (json/yaml/env) |
| `pools` | `list`, `get`, `set`, `delete`, `import`, `export` |
| `assets` | `list`, `details`, `materialize`, `events` |
| `backfill` | `create [--dry-run]`, `list`, `pause`, `unpause`, `cancel` |
| misc | `version`, `health`, `providers list`, `plugins`, `config list`, `config get-value`, `jobs check` |

```bash
datus airflow dags list -p 'sales_%'
datus airflow dags trigger sales_daily -c '{"backfill": false}' --wait
datus airflow tasks logs sales_daily manual__2026-07-05T00:00:00+00:00 load_orders
datus airflow variables set ENV prod
datus airflow connections add pg --conn-uri 'postgres://user:pass@db:5432/warehouse'
```

## Deploying DAGs

```bash
# single file to the profile's dags_folder, then wait until the scheduler parsed it
datus airflow dags deploy ./dags/sales_daily.py --verify sales_daily

# a whole directory to S3, removing remote files that no longer exist locally
datus airflow dags deploy ./dags --dest s3://my-bucket/dags/ --prune -y

# see what would happen first
datus airflow dags deploy ./dags --dry-run

# delete individual files from the target (then drop metadata with `dags delete`)
datus airflow dags undeploy old_dag.py -y
```

- Directories are scanned recursively for `*.py` and `*.zip` (`--all-files`
  to include everything); `__pycache__`, hidden files and `*.pyc` are skipped.
- `--verify <dag_id>` polls the API until that DAG has been re-parsed after
  the upload, and fails fast with the stack trace if the file causes an
  import error. Detection is based on `last_parsed_time` *changing*, so it is
  immune to client/server clock skew.
- `--prune` compares the target against the deployed set and deletes stale
  files — always try `--dry-run` first.
- S3 credentials resolve through the standard boto3 chain (env, shared
  config, instance profile / IRSA) unless overridden in the profile's `s3:`
  block; MinIO and other S3-compatible stores work via `endpoint_url`.
- IAM roles: either point `s3.profile` at an assume-role profile in
  `~/.aws/config`, or set `s3.role_arn` (plus optional `role_session_name` /
  `external_id`) and the plugin performs the STS AssumeRole itself, using the
  chain/profile/keys credentials only to bootstrap it.

## Exit codes

`0` success · `1` runtime/API error (also: run failed under `--wait`,
connection test failed, unhealthy `health`) · `2` usage error · `3` config
error · `8` missing dependency (boto3, if the environment stripped it).

## Development

```bash
pip install -e '.[dev]'
pytest
```

The package never imports `datus`. The whole plugin contract is declared in
`datus_airflow_plugin/datus-plugin.yml` (CLI entry function, bundled skills,
system-prompt template, bash-permission rules, profile config schema); the
entry point `airflow` in the `datus.plugins` group maps the plugin name to the
package. Bundled skills: `airflow` (usage reference for the agent) and
`airflow-setup` (guided configuration).

## Agent bash permissions

The manifest's `permissions` key declares how the Datus agent may run this
CLI through its bash tool (humans in a terminal are never affected):

- **allow everywhere** — read-only commands (`list`/`get`/`details`/`show`/
  `source`/`state`/`logs`, `connections get` masked by default,
  `connections test`, `jobs check`, `version`, `health`, ...).
- **ask under `normal`, allow under `auto`** — reversible routine operations:
  `dags pause/unpause/clear-run`, `tasks clear`, `backfill
  pause/unpause/cancel`, `variables set`, `pools set`, `variables/pools
  export`.
- **ask under both profiles** — anything that starts runs (`dags trigger`,
  `assets materialize`, `backfill create`), ships or removes code
  (`dags deploy`, `dags undeploy`), deletes (`... delete`), bulk-overwrites
  (`... import`), or handles connection secrets (`connections add/export`).

User rules in `agent.yml` always win (deny > ask > allow).
