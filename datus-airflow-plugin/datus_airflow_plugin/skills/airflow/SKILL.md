---
name: airflow
description: Operate a remote Apache Airflow 2.x or 3.x deployment through the `datus airflow` REST CLI
---

# Airflow

`datus airflow` drives a remote Apache Airflow deployment through REST API v1
(Airflow 2.x, Basic Auth) or v2 (Airflow 3.x, JWT). Command groups mirror the
Airflow CLI. Global usage:

```
datus airflow [--profile <env>] <group> <subcommand> [args...]
```

`--profile` (before the group) selects the configured environment; add
`-o json` to any list/get command for full machine-readable output (tables
show a curated column subset). Destructive commands prompt for confirmation —
always pass `-y/--yes` when running non-interactively.

## Profile scope limits

An environment may restrict itself. The `## Airflow` prompt section shows this
per environment as `commands=...` and `dag_prefix=...`; check it before
composing a command rather than discovering the limit by failing.

- **`dag_prefix=<prefix>`** — every `dag_id` argument must start with it.
  Out-of-scope ids exit 2 *without contacting the server*, so a rejection tells
  you nothing about whether that DAG exists. `dags list`, `dags list-runs`
  (across all DAGs) and `assets events` silently drop rows outside the prefix
  and report the count on stderr. Because they carry no `dag_id` to check,
  `assets materialize` and `backfill pause|unpause|cancel` are unavailable —
  use `dags trigger <dag_id>` instead of materializing an asset.
- **`commands=<groups>`** — only those top-level groups exist. Anything else
  exits 2 with a policy error; it is not a typo, do not retry variations.

Not covered by `dag_prefix`: `variables`, `connections` and `pools` are
instance-wide objects in Airflow, so their list/get/export output is never
filtered — treat what you see there as shared with other teams. `dags
list-import-errors` is also unfiltered (errors are keyed by filename, which
does not map to a dag_id).

These limits are guardrails for you, not a security boundary; never work around
one by, say, deploying a file that defines an out-of-scope DAG. If a task
genuinely needs something outside the scope, say so and let the user widen the
profile.

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
Triggering a **paused** DAG exits 2 before creating any run — the scheduler
would leave that run `queued` forever; `dags unpause <dag_id>` first (and ask
the user before unpausing something they did not mention).
Omit `<dag_id>` in `list-runs` to list runs across all DAGs.

## DAG source export and upload

Use the `airflow-dag-export` skill. The Airflow API is the source of truth for
the current active, non-stale DAG set and for Python source. The skill proposes
the full scope by default, supports repeated natural-language filtering, and
must receive explicit confirmation before writing an export destination or
uploading through a storage plugin. This plugin contains no object-storage
client and has no `dags deploy`/`undeploy` command.

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
error.

`assets` and the top-level `backfill` API are Airflow 3/API v2 features. The
Airflow 2/API v1 compatibility path covers DAG, run, task, log, variable,
connection, pool, server-info, and DAG deployment operations.
