---
name: superset-dashboard-authoring
description: Author and safely revise Superset datasets, charts, and dashboards through the official REST API CLI
---

# Superset Dashboard Authoring

1. Inspect the target database, dataset, chart, and dashboard before authoring.
2. Create or refresh the dataset first. Validate SQL with `databases validate-sql` when applicable.
3. Store each official API request body as a project-local JSON file. Create the chart, then attach it with `charts add-to-dashboard <chart-id> --json-file ...`; the update body must preserve the chart's current fields and set the dashboard relationship expected by the installed Superset version.
4. Create or update the dashboard layout only after all chart IDs exist.
5. Read the dashboard and its chart list back, then run representative chart queries.

Never invent IDs. Use `--param q=<Rison filter>` for list filtering. Superset schemas vary across 4.x releases, so obtain the installed schema with `status openapi` when a request is rejected and adapt the JSON body to that schema. All writes require user confirmation.
