# Tableau Source Evidence

Use the existing verified bundle; project only selected chart/dependency records
into dashboard-bootstrap's common source contract. Tableau `assets.json`,
`dependencies.json`, `queries.json` and `coverage.json` are separate documents.
Dependency edges point from consumer to dependency. Inspect full records only
when the bounded `sync inspect --summary` omits required identities or definitions.

## Identity and analytical intent

Keep REST LUIDs, Metadata IDs and workbook-internal names distinct. A workbook can
contain several dashboards and equally named worksheets can occur in different
workbooks. Use exact bundle IDs and edges, not global display-name matching.

For each chart retain native rows/columns, measure aggregations, calculations and
encoding references, filters and their scope/stage, time derivations, marks,
parameters/actions, layout and datasource dependencies. Resolve field-instance
references in their actual worksheet/datasource scope. A mark/layout alone does
not establish the query. LOD, table/window calculations, cross-source relations,
filter order and row-security semantics need supported evidence; keep unsupported
or unresolved semantics as blockers instead of approximating them.

Generated geographical fields are visualization metadata, not physical warehouse
columns. A proposed map-to-bars redesign or sum-to-independent-series change is a
reported difference subject to the common scope/semantic policy. Do not create
extra KPI definitions just because base fields are available.

## SQL provenance and physical connections

Chart `query_status=runtime_sql_unavailable` means Tableau's runtime chart SQL was
not recovered. Dataset Custom SQL is the source relation, not an independently
verified aggregate query for every chart. A complete native chart definition may
supply the missing analytical intent; it cannot erase an explicit coverage blocker.

Preserve every `queries.json` ID, asset relation, origin and file checksum. Multiple
records may contain byte-identical SQL from native and Metadata acquisition.
Deduplicate downstream authoring/indexing only when source physical connection,
dialect and execution context also match; preserve all references in the handoff
and leave the synchronized bundle immutable. Do not turn three references to one
relation into three metric sets or rewrite them as recovered chart SQL.

Resolve each selected dataset's sanitized native connection/relation metadata and
published-datasource lineage. Compare the actual warehouse endpoint/backend and
physical database/catalog with configured Datus datasources. The Tableau server
URL, site, datasource display name or matching table names cannot establish that
identity. A federated dataset can use multiple physical connections; an ambiguous
query-to-connection binding remains unresolved. Source-binding files map exact
native transform IDs to verified target tables, not a whole BI profile to a DB.

## Coverage and processing

Propagate coverage findings along each selected chart's real dependencies.
Metadata API hidden/partial results, unsupported formulas, unresolved published
sources/Prep lineage, external definitions and required unbuilt processing stay
visible. Retrieved SQL in another record does not by itself resolve a
`custom_sql_coverage` issue. Do not replace checksums or edit coverage to proceed.

Only the existing evaluator's documented historical external-ETL exception may
resolve a chart against an independently complete existing DOSI target; its
source status stays partial. See [dosi-rebuild.md](dosi-rebuild.md) for exact
binding evidence. Unknown transformations, security, LOD or metadata/download
failures are not covered by that exception.

A source without Prep processing does not require a fabricated `plan` run. If a
required native flow is present, the documented offline planner writes derived
models and diagnostics only. Missing/unmaterialized required target dependencies
block downstream trials. Synthetic fixtures, local SQL checks, warehouse metric
trials and source-result comparisons are distinct levels of evidence.

## Resolve a Tableau dashboard URL

Match the URL origin to the configured `server_url` before any remote call. For
Cloud/Server URLs such as `/#/site/SITE/views/WORKBOOK/VIEW` or
`/t/SITE/views/WORKBOOK/VIEW`, extract the site content URL and workbook/view tokens
from the actual path/fragment. Decode path components once; query parameters such
as `:showVizHome` are presentation state, not object IDs. Preserve meaningful source
filter/parameter state separately. Never pass the browser URL to `sync run` as an ID.

Run authenticated CLI calls sequentially when using one PAT; a second process
signing in with that PAT can invalidate the first process’s session.
Use `--profile PROFILE` on every call and `--site CONTENT_URL` where the URL specifies
a site. Do not silently choose a site when the URL and profile disagree; use the
URL's explicit site when it belongs to the selected configured origin and access
permits it. If the URL does not specify a site, honor a configured site or ask only
when multiple possibilities remain. Do not call `sites list` unnecessarily for a
site already identified by the URL.

1. Use `datus tableau --profile PROFILE status --site SITE` for access and the real
   `site_luid`, and `workbooks list --site SITE` to match returned `content_url`
   values rather than assume a workbook display name equals its URL token.
2. If the view token needs resolution, use the documented read-only escape hatch
   `api call sites/SITE_LUID/views --site SITE --param pageSize=100` and explicit
   pagination as needed. Match the API's real view `contentUrl`/name and workbook
   ID within the selected site; do not substitute a REST view LUID for a native
   dashboard ID. Avoid site-wide native downloads where a scoped project suffices.
3. Discover native dashboards using `dashboards list --project-id PROJECT_ID
   --site SITE`, matching the verified workbook ID and view name. Reuse the exact
   returned `dashboard:WORKBOOK_LUID:NATIVE_NAME` identity in synchronization.
   Worksheets and generic views are not automatically dashboards.
4. A unique verified match proceeds without repeating the user's source choice.
   Several matches, an unresolved redirect/short link or an unsupported embedded
   URL requires `ask_user` with verified alternatives/canonical URL guidance.
   Authentication, download or metadata errors stay explicit; do not infer identity
   or completeness from an inaccessible browser page.
