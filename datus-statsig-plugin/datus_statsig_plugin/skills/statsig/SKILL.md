---
name: statsig
description: Read Statsig metrics & experiment results, author warehouse-native metric SQL, and drive ETL ingestion via the `datus statsig` CLI (Console API)
---

# Statsig

`datus statsig` drives the Statsig Console API (version `20240601`). Global
usage:

```
datus statsig [--profile <env>] <group> <subcommand> [args...]
```

`--profile` (before the group) selects the configured environment. Output is
JSON by default; add `--compact` for single-line JSON, or `-o table|plain|yaml`
for other formats. Reads run freely; every mutating command confirms before
running.

**Rate limits:** mutations are capped at ~100 requests / 10s and ~900 / 15min
per project — batch write-heavy work and expect HTTP 429 if you exceed them.

## Metrics

```
datus statsig metrics list [-t TAG] [--show-hidden] [--limit N]
datus statsig metrics get <id>                    # or: --name NAME --type TYPE
datus statsig metrics sql <id>                    # the SQL Statsig generates for a metric
datus statsig metrics values --date 2024-09-01 [--metric-name N] [--metric-type T]
datus statsig metrics create --json '{...}' [--dry-run]      # confirms
datus statsig metrics update <id> --json '{...}' [--dry-run] # confirms
datus statsig metrics reload <id> [--incremental]            # WHN recompute, confirms
```

`metrics sql` is the fastest way to see the warehouse SQL behind a metric.

## Warehouse-native metric sources (SQL authoring)

```
datus statsig metric-source list [--limit N]
datus statsig metric-source get <name>
datus statsig metric-source create --json '{...}' [--dry-run]      # confirms
datus statsig metric-source update <name> --json '{...}' [--dry-run]
datus statsig metric-source delete <name>                          # confirms
```

## Composing a write body

Write commands take the request body as `--json '<inline>'` or
`--json-file <path>` (bash cannot pipe stdin). To learn the body shape without
triggering a confirmation prompt, run `describe` (read-only):

```
datus statsig describe metric-source create
datus statsig describe metrics create
```

`describe` prints the same annotated template shown in each write command's
`--help`. Prefer `--dry-run` (adds `"dryRun": true`) to validate a body before
persisting. Missing required fields fail fast with exit 2 naming the field.

Example:

```
datus statsig metric-source create --json '{"name":"purchases","sql":"SELECT user_id, ts, amount FROM prod.orders","timestampColumn":"ts","idTypeMapping":{"userID":"user_id"}}' --dry-run
```

## Experiments (results / analysis)

```
datus statsig experiments list [--status S] [-t TAG] [--limit N]
datus statsig experiments get <id>
datus statsig experiments pulse <id> --control <group> --test <group> [--cuped C] [--confidence X] [--date D]
datus statsig experiments summary <id> [--control G] [--test G]
datus statsig experiments exposures <id> [--dimensional] [--dimension-type T] [--severity S]
datus statsig experiments load-pulse <id> [--refresh]   # trigger WHN Pulse compute, confirms
datus statsig experiments pulse-status <id> [<dag_id>]  # pulse load history / one job
```

Typical WHN flow: `experiments load-pulse <id>` → poll
`experiments pulse-status <id>` → read `experiments pulse <id> --control ... --test ...`.

## ETL ingestion

```
datus statsig ingestion runs [--limit N]
datus statsig ingestion run <id>
datus statsig ingestion status --start 2024-09-01 --end 2024-09-08 [--source S] [--dataset D] [--status ST]
datus statsig ingestion get --type metrics --dataset <dataset> [--source-name N]
datus statsig ingestion schedule-get --dataset <dataset>
datus statsig ingestion backfill --type metrics --dataset <dataset> --start 2024-09-01 --end 2024-09-07 [--source S]  # confirms
datus statsig ingestion schedule-set --dataset <dataset> --hour 3   # scheduled_hour_pst, confirms
```

## Warehouse connections, events, logs, reports

```
datus statsig warehouse-connections update --json-file conn.json   # credentials; file only, confirms
datus statsig events list [--limit N]
datus statsig events get <event_name> [--limit N]
datus statsig logs query [--query EXPR] [--source logs|events|spans] [--start MS] [--end MS] [--limit N] [--after CURSOR]
datus statsig reports get --type pulse_daily|first_exposures|topline_impact_daily --date 2024-09-01
```

`warehouse-connections update` takes credentials — always via `--json-file`
(never inline on the command line); delete the file afterwards.

## Exit codes

0 success · 1 runtime/API error (incl. HTTP 429 rate limit) · 2 usage error
(incl. missing required body field) · 3 profile/config error (e.g. no api_key) ·
8 missing optional dependency.
