# datus-superset-plugin

Independent Datus plugin for Apache Superset 4.x and later.

```bash
datus plugin install src:./datus-superset-plugin
datus superset status health
datus superset dashboards list -o json
datus superset context export-dashboard 42
```

The package registers exactly one plugin (`superset`), has no dependency on
Datus itself, and can be installed without the Grafana plugin.

Profiles support login or bearer-token authentication, TLS verification, and
timeouts. Typed command groups cover the Superset v1 data plane; the guarded
`api call` command provides same-origin access to additional `/api/v1/`
endpoints. State changes, migrations, live queries, raw calls, and context
exports are confirmation-gated by the bundled manifest.

`context export-dashboard` writes one compiled query per SQL file plus a
redacted source snapshot and `manifest.json` under
`reference_sql/superset/<dashboard-slug>/` by default.

## Convert a dashboard into Datus

A request such as “Convert this dashboard into a Datus project dashboard: URL”
loads the host's common `dashboard-bootstrap` skill, then
`superset-query-export` and its `references/datus-rebuild.md`. The guide resolves
Superset dashboard URLs, slugs and permalinks to verified IDs before export.
The common workflow reuses suitable metrics and authors only missing metrics
needed by selected charts before invoking `gen_visual_dashboard`. Explicit
existing-metrics-only and plan-only instructions are retained.

`superset-dashboard-authoring` creates dashboards in Superset; Datus target
authoring uses the common host workflow. Unresolved material source or business
choices go to the main agent's `ask_user` tool after read-only discovery.
This routing requires the updated host skill; version 0.1.1 ships the
Superset-specific guide with the plugin.
