Build a deterministic Grafana dashboard from the already-provisioned
Prometheus datasource, verify a panel query, and export every panel query as
project context.

Perform the workflow directly in the current agent. Do not delegate it to a
subagent or task tool.

Use only typed `datus grafana` commands for Grafana operations. Run each command
in its own Bash tool call. Do not use `curl`, `wget`, `kubectl`, `docker`, direct
Python HTTP, or `datus grafana api call`. Do not delete resources. Use dedicated
file-writing tools for JSON bodies and the final result file; do not use shell
redirects or inline scripts.

Do not run any `--help` command: all command syntax and request bodies needed by
this workflow are provided here. Never use shell metacharacters, including
`|`, `>`, `2>&1`, `;`, `&&`, `||`, backticks, or command substitution.
All five request files are already present and valid immutable inputs. Do not
glob, list, read, rewrite, or edit them.

The environment already contains Grafana 12.1.0, Prometheus 3.5.0, and
node-exporter 1.9.1. Prometheus was preheated for at least 70 seconds. Grafana
has this provisioned datasource:

- name: `E2E Prometheus`
- UID: `prometheus-e2e`
- type: `prometheus`
- URL: `http://prometheus:9090`

In your first assistant turn, issue `status health` and `datasources health
prometheus-e2e` as two separate parallel Bash tool calls. After both succeed,
create the dashboard using exactly:

`datus grafana dashboards create --json-file requests/dashboard.json`

The provided body is a Kubernetes-style Grafana `dashboard.grafana.app/v1beta1`
dashboard document with
metadata name `prometheus-e2e-overview` and a valid initial dashboard `spec`.
The request files are immutable inputs for this task: use them exactly as
provided and do not rewrite or edit them.

The dashboard must have:

- title: `Prometheus E2E Overview`
- UID / metadata name: `prometheus-e2e-overview`
- schemaVersion: `41`
- time range: `now-15m` to `now`
- refresh: `5s`
- one query variable named `job`, datasource `prometheus-e2e`, whose variable
  query is `label_values(up, job)`, with multi-select and include-all enabled

Create exactly four panels using these four separate commands, in order:

`datus grafana panels create prometheus-e2e-overview --json-file requests/panel-1.json`

`datus grafana panels create prometheus-e2e-overview --json-file requests/panel-2.json`

`datus grafana panels create prometheus-e2e-overview --json-file requests/panel-3.json`

`datus grafana panels create prometheus-e2e-overview --json-file requests/panel-4.json`

The provided panel request bodies use datasource
`{"type":"prometheus","uid":"prometheus-e2e"}` for each panel and
one visible target with refId `A`. Preserve the PromQL text exactly:

1. Panel ID 1, title `Targets Up`, Stat panel:
   `sum by (job) (up{job=~"$job"})`
2. Panel ID 2, title `Max Scrape Duration`, Time series panel:
   `max by (job) (scrape_duration_seconds{job=~"$job"})`
3. Panel ID 3, title `Samples Appended Rate`, Time series panel:
   `rate(prometheus_tsdb_head_samples_appended_total[$__rate_interval])`
4. Panel ID 4, title `Resident Memory`, Time series panel:
   `process_resident_memory_bytes{job=~"$job"}`

Give panels non-overlapping `gridPos` values in a two-column layout. Include
`range: true`, `editorMode: code`, and an empty legend format in every target.
Do not interpolate or replace `$job` or `$__rate_interval` in stored queries.

After creating all panels, do not list or read them back because those responses
are large and the independent oracle performs the full stored-state check. Run:

`datus grafana queries run-panel prometheus-e2e-overview 1 --from now-15m --to now`

If the response has HTTP status 200 for refId `A`, treat the bounded query as
verified and proceed immediately. Do not run `panels list` or `dashboards get`.

Then run:

`datus grafana context export-dashboard prometheus-e2e-overview`

The export must use the default `reference_sql` root and contain exactly four
successful `.promql` files, no `.sql` files, while retaining both Grafana
macros.

If the export command returns `total: 4`, `succeeded: 4`, and `failed: 0`, do
not inspect the export directory or run `ls`, `find`, `cat`, or another Bash
command. The independent test oracle validates every file and checksum. Proceed
immediately to the result file.

Finally write `results/grafana-build.json` containing valid JSON with:

- `dashboard_uid` and `dashboard_title`
- `datasource_uid`
- a `panels` object mapping each exact panel title to its numeric panel ID
- `query_verified: true`
- the export command's `output_dir`, `total`, `succeeded`, and `failed`

Use the dedicated `write_file` tool (not Bash) to create the result file. Its
content must be this JSON object, except `output_dir` must be copied from the
successful export response:

```json
{
  "dashboard_uid": "prometheus-e2e-overview",
  "dashboard_title": "Prometheus E2E Overview",
  "datasource_uid": "prometheus-e2e",
  "panels": {
    "Targets Up": 1,
    "Max Scrape Duration": 2,
    "Samples Appended Rate": 3,
    "Resident Memory": 4
  },
  "query_verified": true,
  "export": {
    "output_dir": "<COPY_FROM_EXPORT_RESPONSE>",
    "total": 4,
    "succeeded": 4,
    "failed": 0
  }
}
```

Stop immediately after writing the result file. Do not verify it with Bash.
