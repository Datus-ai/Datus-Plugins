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

Inspect `definition.worksheet` for the actual `column-instance` mappings and
pane axis bindings when the projected rows/columns are insufficient. A bounded
projection of the selected JSON/XML tree is preferable to repeated full reads
or regex guesses about XML structure. Preserve these distinctions explicitly:

- A discrete/ordinal `Month` date-part instance (for example `mn:order_date:ok`)
  groups January across years unless a separate year field also participates.
  It is not a year-month date value or `metric_time` with month truncation.
  Record the exact calendar-part projection and required numeric ordering;
  have `semantic_modeling` author a supported month-of-year dimension when
  missing. Do not replace it with year-month buckets, even when totals agree.
  Carry calendar/week-start/timezone evidence for other date parts; unresolved
  fiscal or week semantics remain blockers. See [Tableau date levels](https://help.tableau.com/current/pro/desktop/en-us/dates_levels.htm).
- A shelf expression containing `+` is not by itself evidence of an arithmetic
  metric. Inspect the worksheet's panes and their `y-axis-name` references: two
  separately bound aggregate axes retain their individual measures. Do not
  manufacture a sales-plus-profit metric from shelf concatenation.
- Preserve declared units; when no currency code/unit is evidenced, mark it
  unknown and omit unsupported currency metadata/formatting. Do not infer USD
  from sales/profit labels, a sample model, locale or a currency symbol.

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
visible. `custom_sql_coverage` means SQL text is missing and remains blocking.
`custom_sql_lineage_incomplete` means Tableau Catalog cannot parse the retrieved
SQL's lineage; it does not establish that PostgreSQL cannot execute that SQL.
Only the evaluator's exact native-SQL qualification below can cover this specific
lineage gap. Do not replace checksums or edit coverage to proceed.

For this qualification, verify the complete Metadata SQL is byte-identical
(ignoring outer whitespace) to every native SQL record of the chart's one
consumed, healthy native dataset. Its single PostgreSQL connection must match
the Metadata DatabaseServer's actual host, port and database, within the same
workbook. The SQL must parse as one SELECT/query without INTO. Missing physical
identity, mixed connections, differing relations or any other unresolved source
semantics remain blockers. Read the exact query, dataset connection and chart
records rather than infer these facts from a summary. Compare that proven source
identity with `get_datasource_identity` before authoring. Once DOSI bindings exist,
`datus tableau rebuild` independently checks the qualification and records
`native_sql_evidence`; metric-engine trials and source-result comparisons are
still required. Keep the original partial source status and lineage warning in
the handoff and final report. Never claim recovered runtime chart SQL or complete
Catalog lineage.

Separately, the existing evaluator's documented historical external-ETL exception may
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
