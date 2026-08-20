# datus-airflow-plugin

A [Datus](https://datus.ai) plugin that drives **remote Apache Airflow 2.x or
3.x** deployments from `datus airflow ...`, backed entirely by the stable REST
API (`/api/v1` for Airflow 2, `/api/v2` for Airflow 3)
— no Airflow installation needed on the client. DAG export is driven by the
bundled `airflow-dag-export` skill from the API's current active DAG set.

```bash
pip install datus-airflow-plugin            # requests + PyYAML
```

> Requires datus-agent >= 0.3.8 — the system-prompt template uses the `config_mutable` render-context variable (older versions skip the whole prompt section).

## Configuration

Profiles live under `agent.plugins.airflow.<profile>` in Datus' `agent.yml`
(`./conf/agent.yml` or `~/.datus/conf/agent.yml`):

```yaml
agent:
  plugins:
    airflow:
      prod:
        default: true
        api_base_url: https://airflow.example.com/api/v1
        api_version: auto                          # URL suffix selects v1; no suffix defaults to v2
        username: admin
        password: ${AIRFLOW_PASSWORD}               # or a static JWT: token: ${AIRFLOW_API_TOKEN}
        dags_folder: s3://my-bucket/dags/           # optional deployment URI;
                                                     # storage credentials belong to the s3 plugin
      staging:
        api_base_url: http://localhost:8080
        username: admin
        password: ${AIRFLOW_STAGING_PASSWORD}
        dags_folder: /opt/airflow/dags
      team_a:                                       # scoped to one team's DAGs
        api_base_url: https://airflow.example.com
        token: ${AIRFLOW_TEAM_A_TOKEN}
        dag_id_prefix: team_a_                      # only team_a_* DAGs
        allow_commands: dags,tasks,version,health   # only these command groups
```

Select an environment with `datus airflow --profile staging ...`; the
`default: true` profile is used otherwise.

### Scoping a profile

Two optional fields narrow what the agent can do in an environment:

| Field | Effect |
|---|---|
| `dag_id_prefix` | Every command taking a `dag_id` rejects ids outside the prefix **before** any request (exit 2); `dags list` / `dags list-runs` / `assets events` filter their output to it. Comma-separate several prefixes. |
| `allow_commands` | Comma-separated allowlist of top-level groups. Groups left out do not exist in the parser at all, and `--help` only shows what remains. Group level only — `dags list` is rejected as a config error, write `dags`. |

With `dag_id_prefix` set, `assets materialize` and `backfill pause|unpause|cancel`
are refused: they take no `dag_id`, so the prefix cannot be checked before the
action happens. Variables, connections and pools are instance-wide in Airflow
and are never prefix-filtered — exclude them via `allow_commands` if a profile
should not reach them.

> **These are agent guardrails, not a security boundary.** Anyone who can edit
> `agent.yml` or call the Airflow REST API directly bypasses them. Real tenant
> isolation has to come from the server: DAG-level RBAC via FabAuthManager, or
> Airflow 3.2+ `[core] multi_team`. The guardrails are complementary to the
> manifest's `permissions` tree — that one classifies commands as auto-run vs.
> confirm for *every* profile, these two limit *which* commands and DAGs a
> single profile sees.

For Airflow 2 API v1, username/password use HTTP Basic Auth. Authentication
for Airflow 3 follows its JWT model: username/password are exchanged
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
| `dags` | `list`, `details`, `list-runs`, `list-import-errors`, `show` (ASCII task tree), `source`, `pause`, `unpause`, `trigger [--wait]`, `state`, `clear-run`, `delete`, `next-execution` |
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

## Exporting and uploading DAG source

Load the bundled `airflow-dag-export` skill. It lists active, non-stale DAGs
through the API, fetches each Python source through `/dagSources`, proposes a
full export by default, and supports repeated filtering by DAG id, tag, owner,
connection id, keyword, or include/exclude rules. It writes nothing to the
requested destination until the user explicitly confirms the current scope.

Uploads are composed by the agent from `dags_folder` or the requested target:
`s3://` uses the S3 plugin, `gs://` GCS, `abfs[s]://`/`adls://` ADLS, and local
paths use filesystem operations. The Airflow distribution does not contain an
object-storage SDK or import another plugin.

## Exit codes

`0` success · `1` runtime/API error (also: run failed under `--wait`,
connection test failed, unhealthy `health`) · `2` usage error · `3` config
error.

## Development

```bash
pip install -e '.[dev]'
pytest
```

The package never imports `datus`. The whole plugin contract is declared in
`datus_airflow_plugin/datus-plugin.yml` (CLI entry function, bundled skills,
system-prompt template, bash-permission rules, profile config schema); the
entry point `airflow` in the `datus.plugins` group maps the plugin name to the
package. Bundled skills: `airflow`, `airflow-dag-export`, and `airflow-setup`.

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
  `assets materialize`, `backfill create`), deletes (`... delete`), bulk-overwrites
  (`... import`), or handles connection secrets (`connections add/export`).

User rules in `agent.yml` always win (deny > ask > allow).
