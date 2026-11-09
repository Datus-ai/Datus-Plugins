---
name: superset-query-export
description: Resolve Superset dashboard URLs and export chart SQL/source evidence for project context or dashboard-bootstrap reconstruction as Datus dashboards
---

# Superset Query Export

For conversion into a Datus project dashboard, load the available
`dashboard-bootstrap` in the main agent before export, then read
[references/datus-rebuild.md](references/datus-rebuild.md). It owns output scope,
necessary additive metric authoring, clarification, task order and completion.
In a visual child, load the skill only for supporting references; do not restart
orchestration. Standalone source export remains available.

Run after source selection and the common workflow's execution gate:

```bash
datus superset context export-dashboard <dashboard-id>
```

The default destination is `reference_sql/superset/<dashboard-slug>/`. It contains one compiled query per `.sql` file, redacted source JSON under `_source/`, and `manifest.json` with dashboard/chart identity, datasource, variables, checksum, and per-query status. Existing output is protected; use `--overwrite` only after reviewing the destination. Add `--include-hidden` when hidden charts are intentionally part of context.

After export, inspect `manifest.json`. A nonzero `failed` count means context is partial; report affected charts. SQL templates/macros are retained where Superset returns them. Never replace failed entries with guessed SQL. Use the files as project-level reference context, not as proof that queries are safe or executable against another database.
