---
name: emr-serverless
description: Operate AWS EMR Serverless applications and run/monitor Spark job runs (with the live Spark UI) via the `datus emr-serverless` CLI
---

# EMR Serverless

`datus emr-serverless` operates EMR Serverless applications and Spark job runs
through boto3. Global usage:

```
datus emr-serverless [--profile <env>] <group> <subcommand> [args...]
```

Add `-o json` for full output. An application must be **STARTED** before it can
run jobs. `jobs run` submits a Spark job (writes data, billed).

## Applications

```
datus emr-serverless applications list [--limit N]
datus emr-serverless applications get <app-id>
datus emr-serverless applications start <app-id>   # warm capacity so it can run jobs
datus emr-serverless applications stop <app-id>
```

## Jobs

```
datus emr-serverless jobs run [<app-id>] --entry-point s3://bkt/job.py \
    [--entry-point-args '["--date","2026-07-01"]'] [--spark-submit-params "--conf spark.executor.memory=4g"] \
    [--execution-role arn:...] [--name my-run] [--wait]
datus emr-serverless jobs list <app-id> [--limit N]
datus emr-serverless jobs run-status <app-id> <run-id>
datus emr-serverless jobs cancel <app-id> <run-id>
datus emr-serverless jobs dashboard <app-id> <run-id>   # live Spark UI URL
```

`<app-id>` and `--execution-role` default to the profile's `application_id` /
`execution_role_arn`. `run --wait` polls until the run is terminal: exit 0 on
`SUCCESS`, 1 otherwise. `dashboard` returns the live Spark UI URL (use it to
inspect logs and stages).

## Exit codes

`0` success · `1` runtime/API error (also: failed run under `--wait`) · `2`
usage (also: missing application id / execution role) · `3` config error.
