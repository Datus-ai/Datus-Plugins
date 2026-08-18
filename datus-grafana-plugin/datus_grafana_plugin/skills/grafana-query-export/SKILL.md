---
name: grafana-query-export
description: Export every Grafana dashboard target—SQL, PromQL, LogQL, TraceQL, Flux, InfluxQL, Graphite, expressions, and unknown JSON—into project context files
---

# Grafana Query Export

Run:

```bash
datus grafana context export-dashboard <dashboard-uid>
```

The default destination is `reference_sql/grafana/<dashboard-slug>/`. Each target gets its own language-appropriate file. Grafana expressions and unknown target models remain structured JSON so information is not discarded. `manifest.json` records dashboard/panel/refId, variables/macros, language, checksum, status, and a credential-free `source_identity` resolved independently for each target from the Grafana datasource API; `_source/` holds redacted dashboard and resolved library-panel documents. Never infer one Datus datasource for an entire Grafana profile or dashboard.

Existing output is protected. Use `--overwrite` only after inspecting the path, and `--include-hidden` only when hidden targets should be context. Retain `$variable`, `${variable:format}`, `$__timeFilter`, interval macros, and datasource templating; do not substitute runtime values. Review the manifest before consuming files as project context and report incomplete resolution rather than guessing query semantics.
