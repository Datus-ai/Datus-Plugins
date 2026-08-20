# datus-mwaa-plugin

A [Datus](https://datus.ai) plugin to inspect **Amazon MWAA**, read current DAG
metadata and Python source through its Airflow REST API, mint tokens, and run
the Airflow CLI over MWAA's REST endpoint.

```bash
pip install datus-aws-plugins
```

> Requires datus-agent >= 0.3.8 — the system-prompt template uses the `config_mutable` render-context variable (older versions skip the whole prompt section).

## Configuration

Profiles live under `agent.plugins.mwaa.<profile>` in Datus' `agent.yml`:

```yaml
agent:
  plugins:
    mwaa:
      prod:
        default: true
        region: us-east-1
        environment: prod-airflow    # optional default environment
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Group | Subcommands |
|---|---|
| `environments` | `list`, `get` |
| `dags` | `list`, `source` |
| `token` | `web-login`, `cli` |
| `cli` | `run '<airflow cli command>'` |

```bash
datus mwaa environments list
datus mwaa dags list --env prod -o json
datus mwaa dags source sales_daily --env prod
datus mwaa token web-login prod       # one-time Airflow UI login URL
datus mwaa cli run 'dags list' --env prod
```

`cli run` is an opaque Airflow-CLI passthrough (the wrapped command could be
destructive) and is always confirmed by the agent — prefer `datus airflow` for
fine-grained, permission-classified DAG operations. Environment
create/update/delete is out of scope.

`dags list/source` use a short-lived MWAA web-login session and Airflow
`/api/v1`. They do not access the environment's S3 bucket. For full or filtered
export, the bundled `mwaa-dag-export` skill proposes an API-derived scope and
allows repeated adjustment; it requires explicit confirmation before writing
or uploading. Destination uploads are performed by the agent through the
matching S3/GCS/ADLS plugin or local filesystem.

## Exit codes

`0` success · `1` runtime/API error (also: MWAA CLI HTTP error) · `2` usage ·
`3` config error.

## Development

```bash
uv run --package datus-mwaa-plugin pytest datus-mwaa-plugin
```

Never imports `datus`; registers the `mwaa` entry point in `datus.plugins`.
Shared AWS API plumbing lives in `datus-aws-common` (plus `requests` for MWAA
and Airflow REST calls). There is no S3 transfer implementation. Bundled
skills: `mwaa`, `mwaa-dag-export`, and `mwaa-setup`.
