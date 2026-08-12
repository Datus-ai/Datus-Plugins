---
name: superset-query-export
description: Export the compiled SQL behind every Superset dashboard chart into project-local reference files and a manifest for project context
---

# Superset Query Export

Run:

```bash
datus superset context export-dashboard <dashboard-id>
```

The default destination is `reference_sql/superset/<dashboard-slug>/`. It contains one compiled query per `.sql` file, redacted source JSON under `_source/`, and `manifest.json` with dashboard/chart identity, datasource, variables, checksum, and per-query status. Existing output is protected; use `--overwrite` only after reviewing the destination. Add `--include-hidden` when hidden charts are intentionally part of context.

After export, inspect `manifest.json`. A nonzero `failed` count means context is partial; report affected charts. SQL templates/macros are retained where Superset returns them. Never replace failed entries with guessed SQL. Use the files as project-level reference context, not as proof that queries are safe or executable against another database.
