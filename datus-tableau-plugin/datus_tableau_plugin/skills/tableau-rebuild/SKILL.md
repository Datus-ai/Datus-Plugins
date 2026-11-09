---
name: tableau-rebuild
description: Interpret verified Tableau source bundles for dashboard-bootstrap, including Tableau-to-DOSI bindings, native semantic gaps and rebuild preflight for Datus visual dashboards
---

# Tableau Guidance for Dashboard Bootstrap

`dashboard-bootstrap` owns the main-agent workflow, output selection, metric policy,
confirmation, builtin task order and acceptance. This skill supplies Tableau source
interpretation and the local binding evaluator. Load the available
`dashboard-bootstrap` skill before main-agent orchestration; do not restart it
when this skill is loaded as its platform guidance. Inside `gen_visual_dashboard`,
load the available common skill to register its supporting-file root, then consume
the exact parent handoff and only the needed references without delegating tasks
or running context/metric authoring. If the common bootstrap skill or required
metric artifact tools are unavailable, report the host capability gap instead of
inventing commands or a second migration workflow.

## Source selection and verification

- For unsynchronized sources, load `tableau-sync`. Honor the supplied profile,
  site/project and dashboard IDs. If no dashboard is named, list local bundles
  under `bi_sources/tableau/`, or use plugin discovery when none exist; present the choices and ask.
  Do not widen a dashboard to its project or site. Explicit scope in the user's
  request is sufficient selection; do not request the same choice again.
- Before consuming local assets, run `datus tableau sync inspect PATH --summary`,
  then inspect exact selected records as needed. Offline inspection needs no login.
  Keep manifest SHA256 and exact dashboard/chart IDs; never bind clipped summary IDs.
- Read [references/source-evidence.md](references/source-evidence.md) to interpret
  native chart semantics, SQL provenance, physical connections and coverage. The
  inspector validates integrity, not analytical completeness.
- Use one installed plugin release for CLI, prompt and skills. If an observed
  skill_location/release differs from the command's installed release, stop and
  report the mismatch; do not repair snapshots or combine versioned contracts.

## Join the common workflow

Follow the selected `dashboard-bootstrap` outputs. A source-only request does not
create a Datus dashboard; a rebuild request does not automatically index SQL or
create metrics. Default to existing DOSI definitions for visual reconstruction.
When the user explicitly requests metrics, the common workflow may run
`semantic_modeling` to inspect/reuse and create missing selected metrics. Complete
that task's validation/reconciliation before destination authoring. Existing
conflicting definitions cannot be overwritten without explicit edit scope.
Source SQL remains evidence, never an automatic target-query fallback.

For a visual output, pass the verified source scope, chart intent, coverage,
allowed differences and this skill's location to `gen_visual_dashboard` through
the common workflow. The child must perform actual metric discovery, then read
[references/dosi-rebuild.md](references/dosi-rebuild.md) for catalogue/binding
formats, `datus tableau rebuild` preflight, supported source-gap resolutions and
Prep artifact handoff. The offline evaluator neither executes DOSI nor creates
or publishes dashboards. It requires `save_metric_query_template` in the target
runtime; do not hand-write `.metric.json` files on older hosts.

## Outcomes

Return exact source-to-target IDs and semantic differences to the common bootstrap
report. Preserve original coverage and distinguish source synchronization, derived
processing definitions, metric validation, actual metric-backed query saves,
render validation and publication. Unresolved native semantics and missing required
dependencies block affected charts while independent charts may continue as partial.
A generated SQL model is not a materialized table; do not execute ETL, refresh
extracts or change Tableau state. A successful preflight or render does not prove
source numerical equivalence.
