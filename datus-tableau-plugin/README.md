# datus-tableau-plugin

Experimental Tableau Server source synchronization for DOSI-driven dashboard rebuilding in Datus SaaS.

```bash
datus plugin install src:./datus-tableau-plugin
datus tableau status                       # auth, API version, Metadata, site_configured
datus tableau sites list                   # discover sites when site_content_url is unset
datus tableau projects list --site Sales   # --site targets or switches a site per command
datus tableau dashboards list --project-id PROJECT_LUID
datus tableau sync run --dashboard-id 'dashboard:WORKBOOK_LUID:DASHBOARD_NAME'
datus tableau sync run --project-id PROJECT_LUID
datus tableau sync run --all
datus tableau sync inspect bi_sources/tableau/SNAPSHOT --summary
datus tableau context export-dashboard 'dashboard:WORKBOOK_LUID:DASHBOARD_NAME'
datus tableau api call sites/SITE_LUID/views --param pageSize=10
```

Every command accepts `-o json|yaml|table` (default `json`). Exit codes: `0` success,
`1` runtime/API error, `2` usage, `3` configuration, `8` missing optional dependency.

## Configuration

Profiles live under `agent.plugins.tableau.<profile>`:

```yaml
agent:
  plugins:
    tableau:
      prod:
        default: true
        server_url: https://tableau.example.test
        auth_mode: token                     # token (PAT) | login (username/password)
        token_name: ${TABLEAU_TOKEN_NAME}
        token_secret: ${TABLEAU_TOKEN_SECRET}
        site_content_url: ""                 # "" = default site; omit to choose later
```

| Field | Required | Default | Notes |
|---|:---:|:---:|---|
| `server_url` | yes | — | HTTP(S) origin without credentials, query or fragment |
| `auth_mode` | yes | `token` | `token` (Personal Access Token) or `login` (username/password), the same vocabulary as the Superset plugin. Tableau Cloud and MFA-enabled servers accept only `token` |
| `token_name`, `token_secret` | with `token` | — | Personal Access Token; `token_secret` is a secret (`x-secret`) |
| `username`, `password` | with `login` | — | Server account; `password` is a secret |
| `site_content_url` | no | *unset* | Site content URL as shown in Tableau URLs (`/#/site/<content_url>/`), not the display name or REST LUID. `""` is the default site. Unset means "not chosen yet" — see [Choosing a site](#choosing-a-site) |
| `api_version` | no | `auto` | Negotiate, or pin a REST version such as `3.21` |
| `verify_ssl` | no | `true` | Keep enabled; use `ca_bundle` for private CAs |
| `ca_bundle` | no | — | Path to a CA bundle file; applied before version negotiation |
| `timeout` | no | `60` | HTTP timeout in seconds |

Secrets must be environment-variable references. Unknown keys are rejected
(`additionalProperties: false`), and the credential pair for the other
`auth_mode` must not be present. One CLI invocation owns one session; use
separate PATs for simultaneous automation, because a second sign-in with the
same PAT can end the first session.

### Choosing a site

A Tableau Server hosts several sites and every asset is site-scoped, so the
plugin never guesses which one you mean:

- **Unset `site_content_url`** means no site has been chosen. `status` and
  `sites list` still work (they sign in to the default site); every other
  remote command refuses with exit code `3` and tells you what to do.
- **`datus tableau sites list`** returns `id`, `name`, `content_url`, `state`
  and `default` for each site so you (or the agent, on your behalf) can pick
  one. Tableau only lets *Server Administrators* list sites; other accounts get
  an explicit message asking them to obtain the content URL from an admin.
- **`--site CONTENT_URL`** on any remote command targets that site for the one
  invocation — use it to switch between sites without editing the profile.
  `--site ""` selects the default site.
- To make the choice permanent, save `site_content_url` in the profile (the
  `tableau-setup` skill does this for you).

`status` reports `site_configured` so the agent can tell which state you are in.

## Command reference

Permission levels follow the manifest: *allow* runs without confirmation under
both the `normal` and `auto` profiles; *ask* requires the user's approval.

| Command | Permission | Needs site | What it does |
|---|:---:|:---:|---|
| `status` | allow | no | Authentication, REST version, Metadata availability, `site_configured` |
| `sites list` | allow | no | List sites to choose or switch `site_content_url` (Server Administrator) |
| `projects list` | allow | yes | Projects in the site |
| `workbooks list [--project-id]` / `workbooks get ID` | allow | yes | Workbooks and their connections |
| `dashboards list [--project-id]` / `dashboards get ID` | allow | yes | Dashboards (scoped `dashboard:<workbook>:<name>` IDs) and their native definition |
| `datasources list [--project-id]` / `datasources get ID` | allow | yes | Published data sources |
| `flows list [--project-id]` / `flows get ID` | allow | yes | Prep flows |
| `sync run --dashboard-id\|--project-id\|--all` | ask | yes | Snapshot source definitions, SQL and ETL dependencies into a checksummed bundle under `bi_sources/tableau/` |
| `sync inspect BUNDLE [--summary]` | allow | offline | Verify a bundle's checksums and schema without credentials |
| `context export-dashboard DASHBOARD_ID` | ask | yes | Snapshot one dashboard's reviewable sources under `reference_sql/tableau/` (the shared BI-plugin project-context convention) |
| `plan --flow FLOW --output DIR` | ask | offline | Derive reviewable SELECT models from a downloaded native Prep flow |
| `rebuild --bundle --bindings --metric-catalog` | allow | offline | Read-only DOSI binding preflight; prints proposed artifact tool calls |
| `api call PATH [--param K=V]` | ask | yes | Read-only, same-origin REST `GET` for resources the typed commands do not cover |

Nothing here writes to Tableau: there is no publish, delete, flow run or
extract refresh, and `api call` only issues `GET`.

The bundled skills (`tableau`, `tableau-setup`, `tableau-sync`,
`tableau-rebuild`) make the agent ask rather than guess: it lists sites when
none is configured, lists projects and then dashboards when you have not named
one, and only uses `sync run --all` when you explicitly ask for the whole site.

Offline processing uses the same managed CLI as server reads. `datus tableau plan`
writes derived processing artifacts from a downloaded native flow, and
`datus tableau rebuild` evaluates explicit DOSI bindings from a verified bundle.
Both run before profile authentication and work with an empty Tableau profile.
In SaaS, invoke these commands through the managed Tableau plugin runtime.

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
filter, join, union, aggregation and columns-to-rows pivot. Native `.v1` and
`.v2018_2_3` shapes with explicit ports and schemas expand containers, loader
actions and super-node annotations into scoped operations. Empty control
containers produce no processing model. Join input sides come from namespace
ports; duplicate right fields receive numeric suffixes. Native fields retain
order and types through removal, rename, replacement, conversion, remap, range
filters, union, single-group unpivot and aggregation. Unknown variants and
ambiguous ports remain blocked locally. Models are never deployed or run;
physical schemas, types and real data remain unverified.

File inputs require an explicit target binding. A CSV filename, Excel relation
or connection attribute is never treated as a warehouse table. Pass
`sync run --source-bindings bindings.json` to bind derived processing artifacts;
the retrieved source assets and native evidence retain their original content.
The JSON object is keyed by full source transform ID, including generated input
files and nested scopes:

```json
{
  "transform:local:input-node": {
    "table": "analytics.orders",
    "columns": ["Order ID", "Sales"]
  }
}
```

`table` accepts a qualified identifier, without SQL fragments. Optional `columns`
assert the exact native field order; differences and unknown source IDs are
rejected. A binding records its explicit origin and hash, while physical schema
verification remains pending. The packaged `schemas/source-bindings-v1.json`
describes the contract. Missing mappings appear in
`pipeline.json.source_bindings_required` and block affected models.

A downloaded native flow can be planned independently of dashboard lineage and
without a Tableau connection:

```bash
datus tableau plan --flow downloaded.tfl --flow-id local \
  --source-bindings bindings.json --output prep-plan --dialect duckdb
```

Omit bindings for an inventory of required mappings. The new output directory
must be inside the current workspace and may not already exist. Output includes
redacted native evidence, assets, dependencies, coverage, transforms, SQL models,
pipeline and validation reports, and `plan-manifest.json` with file checksums
and the original definition hash. This is an offline plan, separate from the
server source-bundle format; inspect its manifest and reports directly. Exit
status is 1 for a partial plan and 2 for invalid input.

The native formula translator uses a bounded typed tokenizer/AST, tagging the
original Tableau expression and translated SQL provenance. Its DuckDB subset
includes typed IF/ELSEIF, IFNULL/ISNULL, integer/string/boolean STR, explicit
`DATEPARSE("dd/MM/yyyy", ...)`, DATE on date/datetime, date components, day
DATEDIFF, positive-index SPLIT and TRIM. Exact native RemoveLetters quick-calcs
map Unicode Letter removal globally. Automatic DATE(string), unknown string
formatting conversions, unsupported functions, LOD, table calculations and
scripts remain blocked. Numeric conversions use null-on-invalid casts and
real-to-integer truncation; division results retain real types. Numeric range
bounds accept exact JSON integers or decimal strings. JSON floating-point bounds
remain blocked because decoding has already discarded their exact decimal token.
Generated input-union files apply their own loader actions and ordered selections
before adding file provenance. Single-group native unpivot retains null rows;
missing referenced fields remain blockers. Native unions retain unresolved
mixed-type and generated Table Names provenance diagnostics rather than
inventing values or implicit conversions. Existing model limits remain 64
ancestors, 256 KiB per model and 8 MiB total SQL; native normalization also caps
nodes, edges and propagated fields.

## Compact verified inspection

```bash
datus tableau sync inspect bi_sources/tableau/SNAPSHOT --summary
```

`--summary` returns JSON within 4096 UTF-8 bytes, including pretty formatting and
the final newline. Every manifest file is still checksum-verified and the complete
source document schemas, asset/query IDs and dependency edges are validated
under the same reader lock. The default inspection response stays the full JSON;
`--summary` requires JSON output and rejects `-o table`.

The summary includes source/selection, manifest SHA256, computed asset/query and
coverage counts, asset kinds, chart IDs/names, coverage code counts, and available
pipeline/validation counts. Lists contain at most 10 items and display text at
most 64 characters; the byte budget can shorten lists further. `omitted_counts`,
`truncation`, `truncated` and `limits` describe every omission or clipped display.
Truncated identities are display hints; use the verified full assets for bindings.
Processing documents larger than 8 MiB are fully checksum-verified but omitted
from the compact processing view with their byte size recorded. No source SQL,
formulas, chart layouts or row data are returned in the summary.

## DOSI-based SaaS rebuilding

The `tableau-rebuild` skill guides source-chart interpretation, existing DOSI
metric discovery, differences and missing dependencies, real metric-tool trial
queries, render generation and the existing SaaS publishing workflow.

Run its deterministic, read-only preflight through the managed CLI:

```bash
datus tableau rebuild \
  --bundle bi_sources/tableau/SNAPSHOT \
  --bindings bi_rebuild/bindings.json \
  --metric-catalog bi_rebuild/metric-catalog.json
```

This command prints JSON only. Use the workspace file tool to save the preflight
report when needed. The CLI performs no metric query and writes no target SQL or
dashboard artifact. A catalogue must come from actual DOSI discovery with its
real model revision; a project without that model remains blocked until a valid
catalogue and target binding are provided.

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
independent SQLite results for processing graphs and ordered LEFT JOINs, synthetic
DuckDB results for native Prep graphs and scalar formulas, DOSI
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

Workbook calculation dependency scanning is linear and limited to 256 KiB and
65,536 lexical tokens per formula. Unclosed field references, literals or block
comments and exceeded limits reject the definition explicitly; dependency
references are never discarded to make a malformed chart appear supported.

Version 0.1.1 exposes offline plan/rebuild through the managed plugin CLI and adds
bounded verified source summaries. Upload the new version rather than replacing
an existing 0.1.0 SaaS installation. Source SQL remains retrieved evidence;
`models/*.sql` is derived SELECT SQL; target metric SQL and query results require
the actual DOSI artifact tool's successful compilation and read validation.

Version 0.1.2 adds native Prep `FilterOperation` formulas through the bounded
typed translator, requiring a boolean result, and accepts the native `GroupBy`
marker for plain aggregate grouping. Missing, malformed and unsupported filter
formulas remain blocked together with their downstream models. The downloaded
native regression flow retains its Completed filter; its generated SELECTs are
checked against CSV inputs in DuckDB. These corrections require a fresh 0.1.2
artifact; an already installed SaaS 0.1.1 artifact does not include them.

Version 0.1.3 adds independently paginated published-datasource Metadata lineage
to bind known upstream Prep flows through stable datasource and flow identities.
An explicit native Hyper create-output marker, an exact datasource-scoped table
identity, and one verified native flow can resolve a Prep materialization's
upstream-definition boundary. Append/update outputs, ambiguous identities, hidden
Metadata and ordinary physical tables retain their coverage gaps. Source input
bindings and processing validation remain required; `upstream_flow` describes
dependency and does not claim a direct producing output node. Installed 0.1.2
artifacts require replacement with a fresh 0.1.3 artifact for this behavior.

Version 0.1.5 aligns the command surface with the other Datus BI plugins:
`context export-dashboard` snapshots one dashboard into `reference_sql/tableau/`
through the same verified `sync` machinery, `api call` is a read-only same-origin
REST escape hatch, and every command renders `json`, `yaml` or `table`.
(0.1.4 was an interim build without these commands.)

Version 0.1.6 makes the site an explicit choice. `site_content_url` is optional:
unset means "not chosen yet" and site-scoped commands refuse with guidance,
while `status` and the new `sites list` sign in to the default site so the site
can be discovered and picked. `--site CONTENT_URL` targets or switches a site per
command. The config schema rejects unknown keys, documents the credential mode as
a strict two-way choice and orders fields by the setup flow. Skills now walk the
user through choosing the site, then the project and dashboard(s). A trial
`serving-target` command and `serving_*` profile fields were removed: a Tableau
site fronts many sources and rebuilds map datasources per asset through source
bindings, so a profile-level serving datasource had no consumer. Profiles that
relied on an omitted `site_content_url` meaning the default site must now set
`site_content_url: ""` explicitly.

Version 0.1.7 renames `auth_type` to `auth_mode` with the values `token`
(Personal Access Token) and `login` (username/password), matching the Superset
plugin, and makes it required. Existing profiles must replace `auth_type: pat`
with `auth_mode: token` and `auth_type: password` with `auth_mode: login`;
the credential field names are unchanged.
