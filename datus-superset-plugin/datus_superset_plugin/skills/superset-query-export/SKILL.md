---
name: superset-query-export
description: Discover stable Superset dashboard query candidates and export selected or complete dashboard SQL into a dashboard-sql-export/v1 manifest. Use for generic dashboard bootstrap, reference SQL, metric initialization, and Superset query-context extraction.
---

# Superset Query Export

This skill is the Superset implementation of the generic dashboard SQL export contract. It only discovers and exports SQL; route exported files to the owning builtin agents outside this skill.

## Select a profile and dashboard

Put the profile before the command on every call:

```bash
datus superset --profile <profile> dashboards list
datus superset --profile <profile> context candidates <dashboard-id>
```

Use the dashboard ID as the stable dashboard identity. `context candidates` is read-only and returns normalized candidate IDs (`chart-<id>`), names, descriptions, hidden/exportable state, and a credential-free `source_identity` resolved through the chart's real Dataset and Database connection. Present these normalized fields before the Generation Manifest. Treat `plugin_metadata` as opaque. Selecting one chart exports every compiled query produced by that chart.

Match each returned `source_identity` to Datus datasources using the generic dashboard-to-metrics rules. Do not infer identity from Dataset table/schema, Database display name, SQL text, or username. One dashboard may return candidates backed by several physical databases.

## Export

Export selected candidates by repeating `--chart-id`:

```bash
datus superset --profile <profile> context export-dashboard <dashboard-id> --chart-id <chart-id> --chart-id <chart-id>
```

Omit `--chart-id` only for an explicitly confirmed full-dashboard export. The default destination is `reference_sql/superset/<dashboard-slug>/`. It contains one compiled query per `.sql` file, redacted source JSON under `_source/`, and `manifest.json`. Existing output is protected; use `--overwrite` only after reviewing the destination. Add `--include-hidden` only when hidden charts were explicitly selected.

## Handoff contract

The manifest declares `contract: dashboard-sql-export/v1`, `plugin`, `profile`, dashboard identity, selection mode, and query entries. Each successful query includes:

- `id`: stable `chart-<id>-query-<index>` identity;
- `candidate_id`: the pre-export `chart-<id>` selection identity;
- `name` and optional `description`;
- `sql_file` plus `checksum` (`sha256:<digest>`);
- query-level `source_identity`, resolved from the chart's Dataset and Database connection metadata without credentials;
- `status: ok`.

Legacy aliases (`platform`, `asset_id`, `asset_title`, `file`, `sha256`) remain available. Failed queries have `status: failed`, a null SQL file/checksum, and an error. Only `ok` queries whose `candidate_id` was confirmed may be routed to builtin agents.

SQL is compiled from the chart's saved `query_context`, and rebuilt from its `form_data` when that is absent — Superset only writes `query_context` for charts saved from Explore, so imported and example charts have none.

After export, inspect `manifest.json`. A nonzero `failed` count means context is partial; report affected charts. Two causes account for most failures: the chart has lost its dataset (`datasource` reads as `None__table`), or its viz type stores dimensions under keys the generic rebuild does not know, as the deck.gl family does. SQL templates/macros are retained where Superset returns them. Never replace failed entries with guessed SQL. Use the files as project-level reference context, not as proof that queries are safe or executable against another database.
