---
name: airflow
description: Operate a remote Apache Airflow 3.x deployment (list/trigger/monitor DAGs, tasks, variables, connections, pools, backfills) and deploy DAG files to S3 or a dags folder via the `datus airflow` CLI
---

# Airflow

`datus airflow` drives a remote Apache Airflow 3.x deployment through its REST
API v2. Command groups mirror the Airflow CLI. Global usage:

```
datus airflow [--profile <env>] <group> <subcommand> [args...]
```

`--profile` (before the group) selects the configured environment; add
`-o json` to any list/get command for full machine-readable output (tables
show a curated column subset). Destructive commands prompt for confirmation —
always pass `-y/--yes` when running non-interactively.

## DAGs

```
datus airflow dags list [-p '%pattern%'] [-t TAG] [--paused|--unpaused] [-o json]
datus airflow dags details <dag_id>
datus airflow dags show <dag_id>              # ASCII task dependency tree
datus airflow dags source <dag_id>            # DAG file source code
datus airflow dags pause|unpause <dag_id>...
datus airflow dags trigger <dag_id> [-c '{"k":"v"}'] [-r RUN_ID] [-l 2026-01-01T00:00:00Z] [--note TEXT] [--wait]
datus airflow dags state <dag_id> <run_id>
datus airflow dags list-runs [<dag_id>] [--state failed] [--limit 20]
datus airflow dags clear-run <dag_id> <run_id> [--only-failed] [--dry-run] [-y]
datus airflow dags delete <dag_id> -y         # removes ALL metadata; confirm with the user first
datus airflow dags next-execution <dag_id>
datus airflow dags list-import-errors
```

`trigger --wait` polls until the run finishes: exit 0 = success, 1 = failed.
Omit `<dag_id>` in `list-runs` to list runs across all DAGs.

## Deploying DAG files

```
datus airflow dags deploy <file-or-dir>... [--dest s3://bucket/dags/ | /path/to/dags]
    [--prefix team_a] [--prune -y] [--all-files] [--dry-run]
    [--verify <dag_id> [--verify-timeout 120]]
```

- `--dest` defaults to the profile's `dags_folder`. S3 targets work out of
  the box (boto3 ships with the plugin).
- Directories are scanned recursively for `*.py` / `*.zip`.
- `--verify <dag_id>` waits until the scheduler re-parsed that DAG and fails
  fast when the deployed file causes an import error — prefer it, it turns a
  blind upload into a checked deployment.
- `--prune` deletes target files not in this deployment — destructive, needs
  `-y`; use `--dry-run` first.

To delete individual files from the target (paths relative to the target
root, as printed by deploy):

```
datus airflow dags undeploy team_a/old_dag.py [--dest ...] [--dry-run] [-y]
```

The DAG goes stale on the next parse; follow with
`datus airflow dags delete <dag_id> -y` to also drop its metadata.

## Tasks

```
datus airflow tasks list <dag_id>
datus airflow tasks state <dag_id> <run_id> <task_id> [--map-index N]
datus airflow tasks states-for-dag-run <dag_id> <run_id>
datus airflow tasks logs <dag_id> <run_id> <task_id> [try_number] [--full-content]
datus airflow tasks clear <dag_id> [-t 'regex'] [-r RUN_ID] [--only-failed] [--dry-run] [-y]
datus airflow tasks failed-deps <dag_id> <run_id> <task_id>
```

Typical debugging flow: `dags list-runs <dag_id> --state failed` →
`tasks states-for-dag-run <dag_id> <run_id>` → `tasks logs <dag_id> <run_id>
<task_id>` → fix → `tasks clear <dag_id> -r <run_id> --only-failed -y`.

## Variables / Connections / Pools

```
datus airflow variables list|get KEY [-d DEFAULT]|set KEY VALUE [-j]|delete KEY|import FILE|export FILE
datus airflow connections list|get ID|add ID (--conn-uri URI | --conn-json '{...}' | --conn-type ...)|delete ID|test [ID]|import FILE|export FILE
datus airflow pools list|get NAME|set NAME SLOTS DESCRIPTION|delete NAME|import FILE|export FILE
```

Connection passwords are masked in output unless `--show-secrets`; exports
contain clear-text secrets — never paste an export back into chat.

## Assets, backfills, server info

```
datus airflow assets list|details --name N|materialize --name N|events [--asset-id N]
datus airflow backfill create --dag-id D --from-date ISO --to-date ISO [--dry-run] | list --dag-id D | pause|unpause|cancel ID
datus airflow version | health | providers list | plugins | config list | config get-value SECTION OPTION | jobs check
```

## Exit codes

0 success · 1 runtime/API error (also: failed run with `--wait`, failed
connection test, unhealthy `health`) · 2 usage error · 3 profile/config
error · 8 missing dependency (boto3, if the environment stripped it).
