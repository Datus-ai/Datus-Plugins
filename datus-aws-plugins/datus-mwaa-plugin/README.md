# datus-mwaa-plugin

A [Datus](https://datus.ai) plugin to inspect **Amazon MWAA** (Managed Workflows
for Apache Airflow) environments, mint web-login/CLI tokens, and run the Airflow
CLI over MWAA's REST endpoint from `datus mwaa ...`. Complements the dedicated
`datus airflow` plugin (which drives Airflow itself).

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
| `token` | `web-login`, `cli` |
| `cli` | `run '<airflow cli command>'` |

```bash
datus mwaa environments list
datus mwaa token web-login prod       # one-time Airflow UI login URL
datus mwaa cli run 'dags list' --env prod
```

`cli run` is an opaque Airflow-CLI passthrough (the wrapped command could be
destructive) and is always confirmed by the agent — prefer `datus airflow` for
fine-grained, permission-classified DAG operations. Environment
create/update/delete is out of scope.

## Exit codes

`0` success · `1` runtime/API error (also: MWAA CLI HTTP error) · `2` usage ·
`3` config error.

## Development

```bash
uv run --package datus-mwaa-plugin pytest datus-mwaa-plugin
```

Never imports `datus`; registers the `mwaa` entry point in `datus.plugins`.
Shared boto3 plumbing lives in `datus-aws-common` (plus `requests` for the CLI
REST call). Bundled skills: `mwaa` and `mwaa-setup`.
