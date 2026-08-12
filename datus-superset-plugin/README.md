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
