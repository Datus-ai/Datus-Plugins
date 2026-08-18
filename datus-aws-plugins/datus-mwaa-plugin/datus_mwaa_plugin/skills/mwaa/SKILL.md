---
name: mwaa
description: Inspect Amazon MWAA, read current DAG metadata/source, mint tokens, and run the Airflow CLI via `datus mwaa`
---

# MWAA

`datus mwaa` inspects Amazon MWAA environments and reaches their Airflow. It
complements the dedicated `datus airflow` plugin: MWAA manages the
**environment**; for fine-grained, permission-classified DAG operations point
`datus airflow` at this environment instead of using `cli run`. Global usage:

```
datus mwaa [--profile <env>] <group> <subcommand> [args...]
```

## Environments

```
datus mwaa environments list
datus mwaa environments get <name>
```

## Tokens

```
datus mwaa token web-login [<name>]   # one-time Airflow UI login URL
datus mwaa token cli [<name>]         # CLI token + web server hostname
```

`<name>` defaults to the profile's `environment`.

## Current DAGs and source

```
datus mwaa dags list [--env NAME] [-o json]
datus mwaa dags source <dag_id> [--env NAME]
```

These commands establish a short-lived MWAA web session and call Airflow's
stable `/api/v1`. `dags list` explicitly requests active, non-stale DAGs;
paused DAGs remain active and are included unless `--unpaused` is requested.
`dags source` resolves the DAG's `file_token` and reads `/dagSources` — it does
not read the MWAA S3 bucket.

For full/selective export or deployment, use `mwaa-dag-export`. It requires
explicit confirmation of the current scope before any destination write.

## Airflow CLI over REST

```
datus mwaa cli run '<airflow cli command>' [--env <name>]
# e.g. datus mwaa cli run 'dags list' --env prod
```

`cli run` mints a CLI token and POSTs the command to
`https://<hostname>/aws_mwaa/cli`, printing the decoded stdout (stderr goes to
stderr). It is an **opaque passthrough** — the wrapped command could be
destructive (`dags trigger`, `variables delete`, ...) — so the agent always
confirms it. Not every Airflow CLI command is supported over MWAA's REST
endpoint (e.g. `dags backfill` is restricted); prefer `datus airflow` for rich
operations.

## Exit codes

`0` success · `1` runtime/API error (also: MWAA CLI HTTP error) · `2` usage
(also: no environment) · `3` config error.
