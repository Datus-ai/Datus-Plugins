---
name: glue
description: Browse the AWS Glue Data Catalog (databases/tables/schema/partitions) and run/monitor Glue crawlers and ETL jobs (with logs) via the `datus glue` CLI
---

# Glue

`datus glue` operates the AWS Glue Data Catalog, crawlers and ETL jobs through
boto3. Global usage:

```
datus glue [--profile <env>] <group> <subcommand> [args...]
```

Add `-o json` to any read command for full output. Crawler runs only touch the
catalog; **job runs write data and are billed** — they always require
confirmation when run by the agent.

## Catalog

```
datus glue catalog databases [--limit N]
datus glue catalog tables <db> [-e EXPR] [--limit N]
datus glue catalog show <db> <table>        # rendered schema: columns, partitions, location, format
datus glue catalog search <keyword>
datus glue catalog partitions <db> <table> [-e EXPR]
datus glue catalog versions <db> <table>
datus glue catalog stats <db> <table> [-c COLUMN ...]
```

Mutations (always confirmed; deletes prompt unless `-y`):

```
datus glue catalog create-database <name> [--description ...]
datus glue catalog delete-database <name> -y
datus glue catalog create-table|update-table <db> --table-input '<TableInput JSON>'
datus glue catalog delete-table <db> <table> -y
datus glue catalog add-partition <db> <table> --partition-input '<PartitionInput JSON>'
datus glue catalog delete-partition <db> <table> --values '["2026","07"]' -y
```

## Crawlers

```
datus glue crawlers list | get <name> | status <name>
datus glue crawlers run <name> [--wait]      # start a crawl (catalog only)
datus glue crawlers stop <name>
datus glue crawlers history <name> [--limit N]
datus glue crawlers metrics [<name> ...]
datus glue crawlers schedule-pause|schedule-resume <name>
```

`run --wait` polls until the crawl returns to `READY`; exit 1 if the last crawl
`FAILED`.

## Jobs

```
datus glue jobs list | get <name>
datus glue jobs run <name> [--args '{"--key":"value"}'] [--wait]
datus glue jobs run-status <name> <run-id>
datus glue jobs runs <name> [--state FAILED] [--limit N]
datus glue jobs stop <name> <run-id> ...
datus glue jobs logs <name> <run-id> [--error]     # reads /aws-glue/jobs/output|error
datus glue jobs bookmark-reset <name>              # causes reprocessing
```

`run --wait` polls until the run is terminal: exit 0 on `SUCCEEDED`, 1
otherwise. Debug flow: `jobs runs <name> --state FAILED` → `jobs logs <name>
<run-id> --error` → fix → `jobs run <name> --wait`.

## Connections

```
datus glue connections list
datus glue connections get <name> [--show-secrets]   # passwords masked by default
```

## Exit codes

`0` success · `1` runtime/API error (also: failed run/crawl under `--wait`) ·
`2` usage · `3` config error.
