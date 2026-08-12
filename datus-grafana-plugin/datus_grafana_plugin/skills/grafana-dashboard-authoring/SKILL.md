---
name: grafana-dashboard-authoring
description: Author and safely revise Grafana dashboards and panels, including datasource resolution and panel query verification
---

# Grafana Dashboard Authoring

1. Inspect dashboard JSON, folder, datasource UIDs, and panel IDs first.
2. Keep dashboard and panel request bodies in project-local JSON files. Use `dashboards create/update` for whole documents and `panels create/update/delete/copy/move` for focused changes.
3. Preserve variables, datasource references, transformations, field configuration, links, and version/resource metadata unless the user explicitly changes them.
4. Use `panels query <dashboard-uid> <panel-id>` with a bounded time range to validate targets after edits.
5. Read the dashboard back and verify panel placement and IDs.

Grafana 12+ `/apis` documents use `metadata` and `spec`; legacy documents use `dashboard` and `meta`. The CLI preserves the detected representation. Never fall back from `/apis` after 401/403. All authoring and live query operations require confirmation.
