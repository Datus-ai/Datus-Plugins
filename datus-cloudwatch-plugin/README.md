# datus-cloudwatch-plugin

A [Datus](https://datus.ai) plugin that queries **AWS CloudWatch** from
`datus cloudwatch ...` — log groups/streams, live tail, **Logs Insights**
queries, metric statistics, alarms and dashboards. Backed by boto3; no local
agent needed.

```bash
pip install datus-cloudwatch-plugin
```

## Configuration

Profiles live under `agent.plugins.cloudwatch.<profile>` in Datus' `agent.yml`
(`./conf/agent.yml` or `~/.datus/conf/agent.yml`):

```yaml
agent:
  plugins:
    cloudwatch:
      prod:
        default: true
        region: us-east-1
        # credentials: omit to use the standard AWS chain (env / ~/.aws /
        # instance profile / IRSA), or set any of:
        profile: my-aws-profile
        role_arn: arn:aws:iam::123456789012:role/datus-readonly
        # access_key_id / secret_access_key / session_token — use ${VAR} refs
```

Select an environment with `datus cloudwatch --profile <env> ...`; the
`default: true` profile is used otherwise. Credentials resolve exactly like the
airflow plugin's S3 block (chain / named profile / explicit keys / STS
AssumeRole via `role_arn`).

## Commands

Everything accepts `-o table|json|yaml|plain`.

| Group | Subcommands |
|---|---|
| `logs` | `groups`, `streams`, `get`, `tail [--follow]`, `insights` (Logs Insights, waits for results) |
| `metrics` | `list`, `get` (statistics over a time range) |
| `alarms` | `list`, `get`, `history`, `set-state` |
| `dashboards` | `list`, `get` |

```bash
datus cloudwatch logs tail /aws/lambda/my-fn --filter ERROR --follow
datus cloudwatch logs insights /aws-glue/jobs/output -q 'fields @message | filter @message like /ERROR/' --start -1h
datus cloudwatch metrics get --namespace AWS/Lambda --name Errors --stat Sum --start -1h -d FunctionName=my-fn
datus cloudwatch alarms list --state ALARM
```

## Exit codes

`0` success · `1` runtime/API error (also: a Logs Insights query that did not
reach `Complete`) · `2` usage · `3` config error.

## Development

```bash
uv run --package datus-cloudwatch-plugin pytest datus-cloudwatch-plugin
```

The package never imports `datus`; it implements the plugin contract
(`run_cli`, `skills_dir`, `system_prompt`, `cli_permissions`) and registers the
entry point `cloudwatch` in the `datus.plugins` group. Shared boto3 plumbing
lives in `datus-aws-common`. Bundled skills: `cloudwatch` (usage reference) and
`cloudwatch-setup` (guided configuration).

## Agent bash permissions

`cli_permissions()` declares how the Datus agent may run this CLI: all read-only
commands (`logs`/`metrics`/`alarms`/`dashboards` list/get/tail/insights) run
everywhere; `alarms set-state` is routine (confirmed under `normal`, auto under
`auto`). User rules in `agent.yml` always win (deny > ask > allow).
