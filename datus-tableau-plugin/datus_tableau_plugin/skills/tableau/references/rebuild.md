# Tableau Guidance for Dashboard Bootstrap

`dashboard-bootstrap` owns the main-agent workflow, output selection, metric policy,
confirmation, builtin task order and acceptance. This reference supplies Tableau
source interpretation and the local binding evaluator through the `tableau` skill.
Load the available `dashboard-bootstrap` skill before main-agent orchestration; do not restart it
when this reference is loaded as its platform guidance. Inside `gen_visual_dashboard`,
load the available common skill to register its supporting-file root, then consume
the exact parent handoff and only the needed references without delegating tasks
or running context/metric authoring. If the common bootstrap skill or required
metric artifact tools are unavailable, report the host capability gap instead of
inventing commands or a second migration workflow.

## Natural-language URL conversion

A request to convert a Tableau dashboard URL into a Datus project dashboard is
sufficient output intent: load the common bootstrap and follow its bounded
execution authorization. Resolve the URL with
[source-evidence.md](source-evidence.md) before asking for
project/dashboard choices already encoded in it. Clarify only remaining material
ambiguities through main-agent `ask_user`; a child returns blockers to its parent.

## Source selection and verification

- For unsynchronized sources, read [sync.md](sync.md). Honor the supplied profile,
  site/project and dashboard IDs. If no dashboard is named, list local bundles
  under `bi_sources/tableau/`, or use plugin discovery when none exist; present the choices and ask.
  Do not widen a dashboard to its project or site. Explicit scope in the user's
  request is sufficient selection; do not request the same choice again.
- Before consuming local assets, run `datus tableau sync inspect PATH --summary`,
  then inspect exact selected records as needed. Offline inspection needs no login.
  Keep manifest SHA256 and exact dashboard/chart IDs; never bind clipped summary IDs.
- Read [source-evidence.md](source-evidence.md) to interpret
  native chart semantics, SQL provenance, physical connections and coverage. The
  inspector validates integrity, not analytical completeness.
- Use one installed plugin release for CLI, prompt and skills. If an observed
  skill_location/release differs from the command's installed release, stop and
  report the mismatch; do not repair snapshots or combine versioned contracts.

## Join the common workflow

Follow the selected `dashboard-bootstrap` outputs. A source-only request does not
create a Datus dashboard; a rebuild request does not automatically index SQL or
create metrics. Conversion into Datus defaults to `create_missing`: reuse existing definitions
and let the common workflow run `semantic_modeling` only for necessary missing
selected metrics. An explicit existing-metrics-only request keeps `reuse_only`. Complete
that task's validation/reconciliation before destination authoring. Existing
conflicting definitions cannot be overwritten without explicit edit scope.
Source SQL remains evidence, never an automatic target-query fallback.

For a visual output, pass the verified source scope, chart intent, coverage,
allowed differences, the loaded `tableau` skill_location and selected reference
paths to `gen_visual_dashboard` through the common workflow. The child must
perform actual metric discovery, then read
[dosi-rebuild.md](dosi-rebuild.md) for catalogue/binding
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
