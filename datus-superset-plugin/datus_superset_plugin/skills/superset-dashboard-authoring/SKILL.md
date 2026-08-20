---
name: superset-dashboard-authoring
description: Author and safely revise Superset datasets, charts, and dashboards through the official REST API CLI
---

# Superset Dashboard Authoring

1. Inspect the target database, dataset, chart, and dashboard before authoring.
2. Settle the dataset's SQL before creating it: resolve the selected Superset Database connection to a uniquely matching Datus datasource using credential-free connection identity, then run the query there so table names, column names, and results are actually confirmed. Never infer one datasource for the whole Superset profile. `databases validate-sql` only parses syntax on PostgreSQL and Presto and happily accepts columns that do not exist, so it cannot serve as this gate. Use `databases table-metadata` to see the column types Superset itself will assign.
3. Store each official API request body as a project-local JSON file. Give `charts create` both `params` and `query_context` — each a JSON *string*, not a nested object. `query_context` is optional to Superset but is what `context export-dashboard` reads, so a chart created without it cannot have its SQL exported later.
4. Attach charts with `charts add-to-dashboard <chart-id> --json-file ...`; the update body must preserve the chart's current fields and set the dashboard relationship expected by the installed Superset version.
5. Create or update the dashboard layout only after all chart IDs exist. Attaching a chart does not place it: the dashboard stays blank until `dashboards update` sets `position_json` (a JSON string) whose CHART nodes carry the right `chartId` and a complete `parents` chain.
6. Read the dashboard and its chart list back, then run representative chart queries with `charts query` to confirm Superset compiles them as intended.

Never invent IDs. Use `--param q=<Rison filter>` for list filtering. Superset schemas vary across 4.x releases, so obtain the installed schema with `status openapi` when a request is rejected and adapt the JSON body to that schema. All writes require user confirmation.
