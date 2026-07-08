---
name: quicksight
description: Manage Amazon QuickSight end-to-end — datasets/datasources/dashboards/analyses/templates/themes/folders lifecycle, users/groups/namespaces, SPICE refresh (with --wait) and schedules, embed URLs, asset-bundle export/import, and account info — via the `datus quicksight` CLI
---

# QuickSight

`datus quicksight` manages QuickSight through boto3. **Every command needs
`aws_account_id`** in the profile; user/group/namespace commands additionally use
the `identity_region`. Global usage:

```
datus quicksight [--profile <env>] <group> <subcommand> [args...]
```

Add `-o json` for full output. Reads run freely; mutations always confirm
(deletes prompt unless `-y`); `refresh run/cancel` are routine.

## Assets (datasets / datasources / dashboards / analyses / templates / themes)

```
datus quicksight datasets   list | describe <id> | permissions <id>
                            | create --cli-input '<JSON>' | update --cli-input '<JSON>'
                            | delete <id> [-y] | set-permissions <id> --cli-input '<JSON>'
datus quicksight datasources list | describe <id> | create/update --cli-input '<JSON>' | delete <id> [-y]
datus quicksight dashboards  list | describe <id> | versions <id> | permissions <id>
                            | create/update --cli-input '<JSON>' | publish <id> <version>
                            | delete <id> [-y] | set-permissions <id> --cli-input '<JSON>'
                            | embed-url <id> --user-arn ARN
                            | embed-url-anonymous <id> [--authorized-resource-arn ARN ...]
datus quicksight analyses   list | describe <id> | create/update --cli-input '<JSON>'
                            | delete <id> [--force] [-y] | restore <id>
datus quicksight templates  list | describe <id> | versions <id> | create/update --cli-input | delete <id> [-y]
datus quicksight themes     list | describe <id> | create/update --cli-input | delete <id> [-y]
```

`create`/`update` (and `set-permissions`, refresh `schedule-put`, `assets`) take
`--cli-input` = the API request body as JSON **without `AwsAccountId`** (the
plugin injects it) — mirrors `aws quicksight ... --cli-input-json`.

## Folders

```
datus quicksight folders list | describe <id> | members <id>
                          | create --cli-input '<JSON>' | delete <id> [-y]
                          | member-add <folder> <member-id> --member-type DASHBOARD|ANALYSIS|DATASET|DATASOURCE
                          | member-remove <folder> <member-id> --member-type ...
```

## Identity (users / groups / namespaces — use `identity_region`)

```
datus quicksight users      list | describe <name> | register --cli-input | update --cli-input | delete <name> [-y]
datus quicksight groups     list | describe <name> | members <name> | create <name> [--description]
                            | delete <name> [-y] | member-add <group> <user> | member-remove <group> <user>
datus quicksight namespaces list | describe <ns> | create <ns> | delete <ns> [-y]
```

## Refresh (SPICE ingestions + schedules)

```
datus quicksight refresh list <dataset-id>
datus quicksight refresh status <dataset-id> <ingestion-id>
datus quicksight refresh run <dataset-id> [--ingestion-id ID] [--wait]
datus quicksight refresh cancel <dataset-id> <ingestion-id>
datus quicksight refresh schedules <dataset-id>
datus quicksight refresh schedule-put <dataset-id> --cli-input '<Schedule JSON>'
datus quicksight refresh schedule-delete <dataset-id> <schedule-id> [-y]
```

`refresh run --wait` polls `describe_ingestion` until terminal: exit 0 on
`COMPLETED`, 1 otherwise.

## Account & asset bundles

```
datus quicksight account settings | subscription
datus quicksight assets export --cli-input '<JSON>' | export-status <job-id>
datus quicksight assets import --cli-input '<JSON>' | import-status <job-id>
```

Asset bundles are the migration path (export a set of assets → import into
another account/namespace). `export`/`import` take the full job request as JSON.

## Exit codes

`0` success · `1` runtime/API error (also: failed ingestion under `--wait`) ·
`2` usage (also: delete without `-y` non-interactively) · `3` config error
(also: missing `aws_account_id`).
