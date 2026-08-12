# datus-grafana-plugin

Independent Datus plugin for Grafana 10–13. Grafana 12+ dashboard APIs are
preferred in `api_mode: auto`, with capability-based fallback to legacy APIs.

```bash
datus plugin install src:./datus-grafana-plugin
datus grafana status health
datus grafana dashboards list
datus grafana context export-dashboard my-dashboard
```

The plugin is independent: it neither imports nor depends on the Superset
plugin, Datus itself, or a shared BI common library. It covers dashboard and
panel authoring, datasources and live queries, annotations, library panels,
playlists, correlations, unified alerting, recording rules, silences, and a
guarded same-origin API escape hatch.

`context export-dashboard` writes each target separately under
`reference_sql/grafana/<dashboard-slug>/`, retaining variables and macros.
SQL, PromQL, LogQL, TraceQL, Flux, InfluxQL, Graphite, Grafana expressions,
and unknown structured queries are represented in their native text or JSON
form alongside a redacted source snapshot and `manifest.json`.
