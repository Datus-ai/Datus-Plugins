---
name: emr
description: Submit and monitor steps on existing Amazon EMR (on EC2) clusters, inspect clusters/instances, and read step logs from S3 via the `datus emr` CLI
---

# EMR

`datus emr` operates **existing** Amazon EMR (on EC2) clusters through boto3.
Cluster provisioning/termination is out of scope (use IaC/ops). Global usage:

```
datus emr [--profile <env>] <group> <subcommand> [args...]
```

Add `-o json` for full output. `steps add` submits billed work.

## Clusters

```
datus emr clusters list [--state WAITING --state RUNNING] [--limit N]
datus emr clusters describe <cluster-id>
datus emr clusters instances <cluster-id>
```

## Steps

```
datus emr steps list <cluster-id> [--state RUNNING] [--limit N]
datus emr steps describe <cluster-id> <step-id>
datus emr steps add [<cluster-id>] --name NAME \
    (--command 'spark-submit s3://b/job.py' | --jar s3://b/app.jar [--arg A --arg B] [--main-class C]) \
    [--action-on-failure CONTINUE|CANCEL_AND_WAIT|TERMINATE_CLUSTER] [--wait]
datus emr steps cancel <cluster-id> <step-id>
datus emr steps logs <cluster-id> <step-id> [--stderr]
```

- `<cluster-id>` for `add` defaults to the profile's `cluster_id`.
- `--command` runs via `command-runner.jar` (args are shell-split); `--jar`
  runs a custom JAR with `--arg` values.
- `add --wait` polls until the step is terminal: exit 0 on `COMPLETED`, 1
  otherwise.
- `logs` reads `s3://<log_uri>/<cluster-id>/steps/<step-id>/stdout.gz` (or
  `stderr.gz` with `--stderr`); requires `log_uri` in the profile.

## Exit codes

`0` success · `1` runtime/API error (also: failed step under `--wait`) · `2`
usage (also: no cluster id / no log_uri) · `3` config error.
