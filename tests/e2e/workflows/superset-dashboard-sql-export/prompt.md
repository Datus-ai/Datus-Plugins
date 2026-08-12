Build a small, deterministic Superset dashboard from the already-provisioned
PostgreSQL table, verify one chart query, and export every chart query as SQL
project context.

Perform the workflow directly in the current agent. Do not delegate it to a
subagent or task tool.

Use only typed `datus superset` commands for Superset operations. Run each
command in its own Bash tool call. Do not use `curl`, `wget`, `psql`, `kubectl`,
`docker`, direct Python HTTP, or `datus superset api call`. Do not delete any
resource. Use the dedicated file-writing tools for JSON request bodies and the
final result file; do not use shell redirects or inline scripts.

Do not run any `--help` command. Never use shell metacharacters, including
`|`, `>`, `2>&1`, `;`, `&&`, `||`, backticks, or command substitution. All
request bodies and exact command syntax are provided below.
The six request files are already present and valid. Do not glob, list, or read
them unless a returned resource ID differs from the expected fixture ID.

The environment already contains:

- a healthy Superset 4.1.2 instance
- an admin profile configured for this run
- one registered database named `E2E PostgreSQL`
- `public.world_population_e2e`, with 16 deterministic rows

In your first assistant turn, issue `status health`, `databases list`,
`datasets list`, and `dashboards list` as four separate parallel Bash tool
calls. Read the database ID returned by the command; never guess it. If the
exact target already exists, inspect and safely reuse it.
Never guess dataset, dashboard, or chart IDs; always read IDs from command
output. This fresh fixture normally assigns database ID 1, dataset ID 1,
dashboard ID 1, and chart IDs 1 through 3. The provided request files contain
those expected IDs. Verify each returned ID before using the next request. If
an ID differs, use the dedicated edit tool to replace only that ID in the
remaining request files; do not rewrite their structure.

Create exactly one physical dataset for:

- database: `E2E PostgreSQL`
- schema: `public`
- table: `world_population_e2e`

After verifying the database ID, run exactly:

`datus superset datasets create --json-file requests/dataset.json`

The create response contains the new dataset ID. Use that response directly;
do not run `datasets list` again after creation.

Create and publish this dashboard:

- title: `Population E2E Overview`
- slug: `population-e2e-overview`

Create it using exactly:

`datus superset dashboards create --json-file requests/dashboard.json`

Create exactly these three charts, all using the physical dataset and all
associated with the dashboard:

1. `Population 2014`: a Big Number showing
   `SUM(population_millions)` filtered to `year = 2014`.
2. `Population by Region 2014`: an ECharts bar chart grouped by `region`, using
   `SUM(population_millions)`, filtered to `year = 2014`, descending by value.
3. `Population Trend`: an ECharts line chart grouped by `year`, using
   `SUM(population_millions)`, ordered by year.

After verifying dataset and dashboard IDs, issue these three commands together
in one assistant turn as three separate parallel Bash tool calls:

`datus superset charts create --json-file requests/chart-1.json`

`datus superset charts create --json-file requests/chart-2.json`

`datus superset charts create --json-file requests/chart-3.json`

Use Superset-native chart request bodies. Each chart must have a valid
`query_context` containing one query and a matching `params` JSON string. Keep
the metric label exactly `SUM(population_millions)`. The query context must use
the datasource object `{"id": <DATASET_ID>, "type": "table"}` and request
JSON/full results. Associate each chart by including the discovered dashboard
ID in its `dashboards` field. If association is not retained by chart creation,
use `charts add-to-dashboard` with a typed update body.

After verifying chart IDs, update `position_json` using exactly:

`datus superset dashboards update <DISCOVERED_DASHBOARD_ID> --json-file requests/dashboard-layout.json`

Do not read the full dashboard or chart list back; the independent oracle
validates their complete stored state. Run
`datus superset charts data <POPULATION_2014_CHART_ID>`; it must return data and
compiled SQL.

Then run:

`datus superset context export-dashboard <DISCOVERED_DASHBOARD_ID>`

The export must be written under the default `reference_sql` root and must
contain exactly three successful `.sql` files.

If export returns `total: 3`, `succeeded: 3`, and `failed: 0`, do not inspect
the export directory with Bash. Proceed immediately to the result file.

Finally write `results/superset-build.json` containing valid JSON with:

- `dashboard_id`, `dashboard_title`, and `dashboard_slug`
- `database_id` and `dataset_id`
- a `charts` object mapping each exact chart title to its discovered chart ID
- `query_verified: true`
- the export command's `output_dir`, `total`, `succeeded`, and `failed`

Use the dedicated `write_file` tool, not Bash, for the result. Stop immediately
after writing it; do not perform exploratory or verification commands once the
target and export are verified.
