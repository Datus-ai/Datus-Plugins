# Superset Source to Datus Dashboard

This is platform guidance for the single `dashboard-bootstrap` workflow. The
requested target is a Datus project dashboard, not a new dashboard on Superset.
Keep source operations read-only apart from the local export; do not create,
update, import, publish, refresh or delete source BI artifacts.

## Resolve a pasted URL before export

Match the origin (and deployment path prefix) to an enabled profile's
`api_base_url`. Honor a named profile. Several matching profiles require a
main-agent `ask_user` choice; do not select another origin or put credentials in
arguments. Preserve user-supplied meaningful filter/time context separately from
identity and explain any unsupported state.

- For `/superset/dashboard/<id-or-slug>/`, read
  `datus superset --profile PROFILE dashboards get ID_OR_SLUG -o json`.
  Use the actual response's stable dashboard ID for later calls; do not assume a
  URL token, title or redirect is itself a verified numeric ID.
- For `/superset/dashboard/p/<key>/`, use the existing read-only
  `dashboards permalink-get KEY` command, then verify the resolved dashboard
  with `dashboards get`. Do not call `permalink-create`, which writes source state.
- Resolve canonical ID and current chart/dataset inventory through `dashboards
  charts ID`, `dashboards datasets ID` and `charts get ID` as needed. Follow actual
  returned IDs. If an embed/short link cannot be resolved through the installed
  API, ask for a canonical accessible dashboard URL; do not scrape a login page.

In each command above place `--profile PROFILE` before the command group. A URL
that matches no configured profile requires connection configuration, not guessed
network access. A 401/403 is an access problem, not evidence of an empty dashboard.

## Export and preserve analytical intent

After the common workflow's selected-scope gate, run its documented
`context export-dashboard DASHBOARD_ID` once. Read the returned output directory
and manifest; verify selected dashboard/chart IDs, query statuses, files and
checksums. Preserve chart-to-query/source relationships and failures. Only
successful complete selected SQL is input to reference indexing or semantic work.
Do not rewrite failed queries or silently mark them complete.

Read bounded selected `_source/` chart/dataset records to retain measures,
aggregations, calculated/adhoc expressions, groupings, time grain/timezone,
filters and their scope (dashboard/native vs chart), parameters, limits and layout.
SQL is evidence for these definitions; do not turn a sample date/limit into a
permanent metric. Preserve multi-query charts and dataset/database connection
identities; the Superset server endpoint is not the warehouse endpoint. Use the
common source contract to match real physical connections before authoring.
Missing template variables, filters/security, unsupported chart calculations or
ambiguous connections stay per-chart blockers rather than guessed semantics.

## Target handoff

A conversion selects `create_missing` unless the user requests existing metrics
only. Reuse maintained definitions and let `semantic_modeling` author only missing
selected metrics. Never overwrite conflicting definitions without resolving the
business choice with the main agent's `ask_user`. Complete validation and metric
rediscovery before `task(type="gen_visual_dashboard", ...)`.

Pass exact source scope/checksums, complete successful SQL, bounded original chart
intent and filters, target datasource/model, metric identities, blockers and
allowed differences. Load common visual/validation references in the destination
node. Use its actual metric discovery and `save_metric_query_template`; normal
SQL fallback needs explicit scope and the existing artifact-tool contract.
Exported files, compiled SQL, query trials, rendered preview and publication are
separate outcomes. There is no Superset-specific DOSI preflight command here;
do not invent a `superset rebuild` API. Follow common per-chart validation and
reporting, retaining partial coverage and source-equivalence limitations.
