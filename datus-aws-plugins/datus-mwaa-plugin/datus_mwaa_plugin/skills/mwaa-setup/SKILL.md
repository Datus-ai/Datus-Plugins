---
name: mwaa-setup
description: Configure an environment profile for the `datus mwaa` plugin (AWS region + credentials, optional default MWAA environment name)
requires_mutable_config: true
---

# MWAA Setup

Use this skill when `datus mwaa` is installed but has no configured environment.

## Config structure

Profiles live under `agent.plugins.mwaa.<profile>` in the config file named by
the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    mwaa:
      prod:
        default: true
        region: us-east-1
        environment: prod-airflow        # optional default MWAA environment name

        # credentials — omit to use the standard AWS chain, otherwise any of:
        profile: my-aws-profile
        role_arn: arn:aws:iam::123456789012:role/datus-mwaa
        # access_key_id / secret_access_key — use ${VAR} refs
```

## Steps

1. Ask for `region` and optionally a default `environment` name.
2. The principal needs `airflow:ListEnvironments`, `airflow:GetEnvironment`,
   `airflow:CreateWebLoginToken`, and `airflow:CreateCliToken`.
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with `datus mwaa environments list`.

## Related

For day-to-day DAG operations (list/trigger/monitor with per-command
permissions), configure the **`datus airflow`** plugin against this MWAA
environment (use `datus mwaa token cli` for the hostname/token, or the web
login URL from `datus mwaa token web-login`).

## Troubleshooting

- `no environment` — pass the environment name or set `environment` in the
  profile.
- `cli run` returns an error for some commands — MWAA's REST CLI does not
  support every Airflow subcommand; use `datus airflow` instead.
