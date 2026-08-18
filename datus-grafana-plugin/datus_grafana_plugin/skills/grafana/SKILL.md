---
name: grafana
description: Operate Grafana dashboards, panels, datasources, queries, annotations, library panels, playlists, correlations, and unified alerting through `datus grafana`
---

# Grafana

1. Check `datus grafana status health` and `status whoami`.
2. Discover resources before mutation with dashboard search, datasource list/health, panel list, and alert-rule reads.
3. Use project-local JSON request bodies through `--json-file`; do not place tokens or datasource secrets in them.
4. Prefer typed commands. Use guarded `api call METHOD /api/...` or `/apis/...` only for official APIs without a typed command.
5. Read back any changed object and run a bounded panel/datasource query where relevant.

Grafana 12+ dashboard operations prefer the Kubernetes-style `/apis` API. In `api_mode: auto`, only a 404/405 capability failure falls back to legacy `/api`; authorization failures never fall back. Writes, query execution, exports, and raw API calls require confirmation.

Use `grafana-dashboard-authoring` for layout work and `grafana-query-export` for project context. Resolve datasource identity per panel target because one Grafana instance and one dashboard can span multiple datasources.
