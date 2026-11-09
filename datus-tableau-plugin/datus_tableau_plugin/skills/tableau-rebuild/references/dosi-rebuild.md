# Tableau DOSI Binding and Processing Contract

Read inside `gen_visual_dashboard` after the main agent has completed any selected
metric authoring. For a source-only/context request, read this only if a binding
or Prep contract is actually needed.

## Match analysis intent to existing DOSI

Inside `gen_visual_dashboard`, use its actual `list_metrics` / `search_metrics`
and `get_metric` tools. Copy full metric identities as `{path: [...], name: ...}`
from discovery. Inspect the real metric's dimensions, required dimensions, time
grains, parameters and datasource. The active datasource and DOSI model binding
must be unambiguous; never silently switch shared runtime datasource state.

For each chart compare its goal, aggregation, distinctness, numerator/denominator,
time axis/grain, timezone, null policy, filters and their stage/scope, LOD,
window and table calculations with the discovered metric. Record semantic
differences explicitly. A similar label is not enough evidence to select a
metric. Missing or ambiguous metrics, invalid grain/dimensions, missing external
ETL, unsupported calculations, actions or row-security semantics block the
affected chart. Permission and query failures are not reasons to execute source
SQL instead. Other independent charts may continue and remain marked partial.

## Deterministic handoff report

`datus tableau rebuild` is the managed offline, read-only evaluator. It verifies
the source bundle, checks explicit bindings against a discovered metric catalogue
and produces proposed calls for the existing artifact tool. It does not execute
the metric engine, create a target dashboard or prove numerical equivalence.

Write normalized catalogue entries only from actual DOSI discovery and the
active model file's real SHA256. `dimensions` and `parameters` below are names
projected from the tool's returned detail, not new model definitions. Keep any
tool-result references in the mapping review notes; never invent a revision,
datasource, identity or supported capability.

### metric-catalog.json

```json
[
  {
    "metric": {"path": ["Commerce", "Revenue"], "name": "net_revenue"},
    "datasource": "warehouse",
    "model_revision": "REPLACE_WITH_REAL_MODEL_SHA256",
    "dimensions": ["metric_time", "region"],
    "required_dimensions": [],
    "time_granularities": ["day", "week", "month"],
    "parameters": []
  }
]
```

### bindings.json

```json
{
  "schema_version": 1,
  "source_manifest_sha256": "REPLACE_WITH_VERIFIED_MANIFEST_SHA256",
  "charts": [
    {
      "chart_id": "REPLACE_WITH_EXACT_SOURCE_CHART_ID",
      "differences": ["Current DOSI revenue excludes refunds according to the maintained metric definition."],
      "queries": [
        {
          "name": "revenue_by_region",
          "datasource": "warehouse",
          "model_revision": "REPLACE_WITH_REAL_MODEL_SHA256",
          "goal": "Show current net revenue by region for the selected dates",
          "hypothesis": "Regional revenue comparisons explain the original chart's business question",
          "query": {
            "metric": {"path": ["Commerce", "Revenue"], "name": "net_revenue"},
            "dimensions": ["region"],
            "time_start": {"param": "start_date"},
            "time_end": {"param": "end_date"},
            "filters": [],
            "limit": 10000
          },
          "params": [
            {"name": "start_date", "type": "date", "required": true},
            {"name": "end_date", "type": "date", "required": true}
          ],
          "sample_params": {"start_date": "2026-01-01", "end_date": "2026-02-01"}
        }
      ]
    }
  ]
}
```

Replace every placeholder and synthetic example with verified project values.
Use an empty `differences` list when the comparison finds none; never copy the
example's business difference as a fact. Keep these files under the project.
Invoke it through the managed Tableau plugin runtime:

```bash
datus tableau rebuild \
  --bundle bi_sources/tableau/VERIFIED_BUNDLE \
  --bindings bi_rebuild/bindings.json \
  --metric-catalog bi_rebuild/metric-catalog.json
```

The command works without Tableau login and prints JSON to stdout. It requires
an actually discovered metric catalogue and its real datasource/model binding;
an empty or unrelated project model is not evidence that a target metric exists.
The command prints JSON to stdout. Exit 0 means every chart is
`ready_for_metric_validation`; exit 1 includes blocked/partial plans or invalid
inputs. Persist the report with the workspace file tool and inspect every chart's
`reasons`, `differences`, `metric_bindings` and proposed `tool_calls`.
Absent chart bindings remain blocked. `combination: "independent"` is reserved
for genuinely independent KPI values. Joining several metric results requires
separate runtime proof of equal datasource/keys/grain/filter context and key
uniqueness; the evaluator blocks that unproven alignment.

One narrowly scoped exception is a historical upstream ETL definition that is
unavailable but is not required by an independently complete existing DOSI target.
Keep the source synchronization partial. For that chart only, add
`source_gap_resolutions` entries containing the exact external-source `asset_id`,
`code` (`external_etl_unresolved` or `external_definition_required`),
`resolution: "existing_dosi_target"`, concrete `evidence` of the existing target
dataset and a `metric_bindings` list of `{metric, datasource, model_revision}`
matching every discovered target query. The report preserves
`historical_definition_not_synchronized` and pending engine validation. This does
not waive unknown transformations, LOD, security, metadata/download failures,
or unbuilt target dependencies, and cannot be used to claim the historical ETL
was synchronized. The actual metric tool trial is still required.

## Apply the verified preflight

The common bootstrap visual reference owns delegation and destination acceptance.
Inside the visual node, use only proposed calls from ready chart records and real
`save_metric_query_template` results. Recheck the source checksum and model revision
before saves. Preserve blocked charts and the evaluator's source-gap resolutions
in the completion report. Its ready status is not an executed metric or a published
dashboard. Follow the common workflow for parameter trials, result alignment,
render/interactions and publication.

## Dataset and ETL handoff

For a downloaded native flow, invoke
`datus tableau plan --flow FLOW --source-bindings FILE --output DIR --dialect DIALECT`
through the same managed CLI. This writes checked derived processing artifacts in
a new workspace directory; it does not execute them. Inspect its pipeline and
validation findings before proposing any target dependency.

Read synchronized dataset/transform definitions and generated `models/*.sql`,
`pipeline.json` and validation findings. First reuse existing DOSI datasets,
relationships and upstream tables that satisfy the metric. Generate only the
additional processing artifacts required for the selected target, preserving
the source-node mapping, dependency order, dialect and parameter requirements.
Unknown transforms, missing dependencies, unresolved field types, incompatible
LOD/window/filter order and external scripts remain explicit blockers.

Do not execute ETL, create tables, deploy a scheduler, start a Prep Flow, refresh
an extract or update the source BI platform. A generated model is not a
materialized dataset. If target queries require an unbuilt model, report the
dependency and stop those queries rather than routing back to old source SQL.
