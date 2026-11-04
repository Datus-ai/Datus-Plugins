# datus-tableau-plugin

Experimental Tableau Server source synchronization for DOSI-driven dashboard rebuilding in Datus SaaS.

```bash
datus plugin install src:./datus-tableau-plugin
datus tableau status
datus tableau dashboards list
datus tableau sync run --dashboard-id 'dashboard:WORKBOOK_LUID:DASHBOARD_NAME'
datus tableau sync run --project-id PROJECT_LUID
datus tableau sync run --all
datus tableau sync inspect bi_sources/tableau/SNAPSHOT
```

Profiles live under `agent.plugins.tableau.<profile>`:

```yaml
agent:
  plugins:
    tableau:
      prod:
        default: true
        server_url: https://tableau.example.test
        site_content_url: ""
        auth_type: pat
        token_name: ${TABLEAU_TOKEN_NAME}
        token_secret: ${TABLEAU_TOKEN_SECRET}
```

Password authentication uses `auth_type: password`, `username`, and `password: ${TABLEAU_PASSWORD}`. Optional settings are `api_version: auto`, `verify_ssl: true`, `ca_bundle` and `timeout: 60` seconds. The default site has an empty content URL, not its REST LUID. Configure TLS options before version negotiation. One CLI invocation owns one session; use separate PATs for simultaneous automation.

The plugin reads metadata and native definitions. It never publishes to Tableau, runs Prep, refreshes extracts, or deploys transformation SQL. `sync run` writes a checksummed workspace-local snapshot; `sync inspect` validates it offline without credentials. Existing snapshots require `--overwrite`.

Metadata API must be enabled and accessible. Native download permissions are needed for complete layouts, calculations and processing definitions. Downloads exclude extracts where the API supports that option; packaged flow data is discarded during definition parsing. Unsupported transformations, hidden lineage and failed downloads are reported explicitly. A source bundle is not proof of complete runtime SQL recovery or numerical equivalence.

Use the bundled `tableau-rebuild` skill to map source chart intent to existing DOSI metrics and generate a SaaS dashboard using the existing Datus artifact tools. Source SQL is evidence; it is never a silent fallback for an unmatched metric. Missing metrics, ambiguous source identity and unsupported calculations remain blocked in the mapping report.

Native worksheet checks follow exact datasource-qualified field and column-instance
bindings through used calculation dependencies, including worksheet-local fields.
An indirect LOD or table calculation blocks its consuming chart; unused calculation
metadata does not block other charts. This includes `TOTAL`, `PREVIOUS_VALUE`,
window/running/rank functions, predictive model table calculations and analytics
extension calls documented in Tableau's [table calculation functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_tablecalculation.htm).
Quoted strings, date and numeric literals (including scientific notation), and
comments follow Tableau's [calculation syntax](https://help.tableau.com/current/pro/desktop/en-us/functions_operators.htm)
and do not create field references or function calls. Ordinary aggregates remain
supported. Missing generated calculation identities or instance targets, incomplete
or conflicting calculation definitions, ambiguous field scope and dependency cycles
produce explicit chart coverage gaps. Sparse physical column metadata remains
allowed, including undeclared SQL output fields; this bounded dependency check is
not a complete Tableau formula compiler.

Tests are deterministic, using SDK doubles and native source fixtures. No production Tableau Server compatibility or real SaaS deployment is claimed until a target environment is verified.

## Source bundle and processing output

`sync run` prints the actual `output_dir`. Its directory name is derived from the
server, site and selection, so separate selections do not overwrite each other.
The snapshot contains:

- `manifest.json`: source identity, selection, status, summary and file SHA256s.
- `assets.json`, `dependencies.json`, `queries.json`, `coverage.json`: the
  versioned source graph, query provenance and object-level coverage findings.
- `native/` and `metadata/`: sanitized native definitions and observed lineage.
- `sql/`: retrieved source SQL, explicitly distinguished from reconstructed SQL.
- `transforms/`, `models/`, `pipeline.json`, `validation.json`: operation
  definitions, generated SELECT models, processing dependencies and validation.

The schemas under `datus_tableau_plugin/schemas/` describe the bundle contract.
Shared dependencies are deduplicated; source identifiers retain their workbook
and platform context. Missing upstream definitions, unavailable downloads and
unsupported calculations remain explicit. A partial snapshot is saved for
inspection and `sync run` returns exit code **1**; it does not mean every object
failed. A failed update preserves the previous usable snapshot.

Use `--external-source relative/path.sql` to retain external SQL evidence, or
`--external-source relative/definitions.json` to provide a definition pack with
the same `assets`, `dependencies`, `queries` and `coverage` structure. Plain SQL
does not automatically establish which physical table it produces. An explicit
definition pack can resolve an observed external-source boundary using its exact
asset ID and a `base_source`, `etl_definition`, or processing `operation`. It
cannot replace an observed dashboard, chart or dataset definition.

`--dialect` selects the processing SQL dialect (default `duckdb`). SQL models are
generated only where operation semantics and ordered inputs are explicit.
Supported mapped operations include source, projection, rename, derivation,
filter, join, union, aggregation and columns-to-rows pivot. Native Prep
containers, unknown scripts and ambiguous join direction are retained and
blocked. Models are never deployed or run; physical schemas, types and real data
remain unverified unless subsequently checked through the existing Datus tools.

## DOSI-based SaaS rebuilding

The `tableau-rebuild` skill guides source-chart interpretation, existing DOSI
metric discovery, differences and missing dependencies, real metric-tool trial
queries, render generation and the existing SaaS publishing workflow.

Its deterministic preflight helper also runs independently:

```bash
python -m datus_tableau_plugin.rebuild \
  --bundle bi_sources/tableau/SNAPSHOT \
  --bindings bi_rebuild/bindings.json \
  --metric-catalog bi_rebuild/metric-catalog.json
```

The skill includes complete input examples. Bindings pin the source manifest
SHA256 and the discovered DOSI metric's full subject identity, datasource and
model revision. The helper prints proposed `save_metric_query_template` calls;
the real Datus artifact tool owns compilation, query execution and captured
model snapshots. `ready_for_metric_validation` means preflight passed, not that
the dashboard was rendered or published. Older Datus runtimes without
`save_metric_query_template` cannot complete the rebuild workflow.

## Development validation

```bash
uv sync --package datus-tableau-plugin --extra dev
uv run --package datus-tableau-plugin pytest datus-tableau-plugin -q
uv build --package datus-tableau-plugin --wheel
```

The offline suite includes real TSC HTTP serialization with synthetic responses,
native XML/ZIP parsing, shared assets and failed snapshot updates, SQL provenance,
independent SQLite results for processing graphs and ordered LEFT JOINs, DOSI
binding checks, and packaged-plugin contracts. Live Server access, real DOSI
warehouse queries and SaaS rendering/publication require a configured project
and are not exercised by these offline tests.

### Snapshot coordination and recovery

Concurrent snapshot access currently requires POSIX `flock` support (Linux/macOS).
Readers take a shared lock and writers an exclusive lock on the stable parent
directory. Writes to sibling bundles in that directory therefore serialize too;
inspection creates no lock files and never modifies a snapshot.

If a process terminates between the two directory renames, inspection can read the
single checksum-verified previous snapshot in place. Its result includes the actual
`resolved_path`, `manifest_sha256`, and `recovered_from_backup`; rebuilding uses that
verified manifest identity. The next authorized writer can restore the same-source
backup. Multiple, corrupted, symlinked or differently sourced backups require
explicit recovery; no backup is chosen by timestamp.

Processing generation uses iterative graph traversal. A model may expand at most
64 ancestors, 256 KiB of SQL, and the complete generated output at most 8 MiB of SQL.
Limit violations and their downstream consumers are reported as blocked, while
independent models can continue within the remaining budget. Rebuild dependency
inspection is bounded to depth 64 and 4096 visited nodes per chart and reports a
resource blocker instead of exhausting the Python stack.
