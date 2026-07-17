# datus-emr-serverless-plugin

A [Datus](https://datus.ai) plugin to operate **AWS EMR Serverless**
applications and run/monitor Spark job runs from `datus emr-serverless ...`.
Backed by boto3.

```bash
pip install datus-aws-plugins
```

> Requires datus-agent >= 0.3.8 — the system-prompt template uses the `config_mutable` render-context variable (older versions skip the whole prompt section).

## Configuration

Profiles live under `agent.plugins.emr-serverless.<profile>` in Datus'
`agent.yml`:

```yaml
agent:
  plugins:
    emr-serverless:
      prod:
        default: true
        region: us-east-1
        application_id: 00abc123        # optional default application
        execution_role_arn: arn:aws:iam::123456789012:role/emr-serverless-exec
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Group | Subcommands |
|---|---|
| `applications` | `list`, `get`, `start`, `stop` |
| `jobs` | `run [--wait]`, `list`, `run-status`, `cancel`, `dashboard` |

```bash
datus emr-serverless applications start 00abc123
datus emr-serverless jobs run --entry-point s3://bkt/job.py --wait
datus emr-serverless jobs dashboard 00abc123 jr-456
```

`jobs run` writes data and is billed (always confirmed by the agent);
`applications start/stop` and `jobs cancel` are routine.

## Exit codes

`0` success · `1` runtime/API error (also: failed run under `--wait`) · `2`
usage · `3` config error.

## Development

```bash
uv run --package datus-emr-serverless-plugin pytest datus-emr-serverless-plugin
```

Never imports `datus`; registers the `emr-serverless` entry point in
`datus.plugins`. Shared boto3 plumbing lives in `datus-aws-common`. Bundled
skills: `emr-serverless` and `emr-serverless-setup`.
