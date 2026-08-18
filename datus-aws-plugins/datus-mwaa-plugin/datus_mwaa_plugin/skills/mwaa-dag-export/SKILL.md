---
name: mwaa-dag-export
description: Export current MWAA DAG Python sources with an adjustable, explicitly confirmed scope, then optionally upload them through the storage plugin selected by the destination URI
---

# MWAA DAG Export

Use this skill when a user asks to export, copy, migrate, back up, or deploy
DAG Python files from Amazon MWAA. Planning and confirmation are driven by the
LLM; there is no export-plan or export CLI command.

## Non-negotiable rules

- The live Airflow API behind MWAA is the only authority for candidate DAGs
  and Python source. Never enumerate the MWAA S3 DAG prefix for discovery.
- Default to every active, non-stale DAG from `datus mwaa dags list -o json`.
  Paused DAGs are active and included.
- Fetch source with `datus mwaa dags source <dag_id>` through Airflow
  `/dagSources`; never use S3 `GetObject` or `datus s3` as a source fallback.
- Temporary staging is allowed for inspection. Do not write the requested
  destination or upload until the user explicitly confirms the exact proposal.
- Every scope adjustment invalidates confirmation. Recompute, show, and ask
  again. Never prune or delete destination files.

## 1. Discover and stage from the API

1. Resolve the MWAA profile and environment name.
2. Run the unbounded current-DAG listing:

   ```text
   datus mwaa [--profile ENV_PROFILE] dags list [--env MWAA_ENV] -o json
   ```

3. Keep active, non-stale DAGs; include paused DAGs unless the user requests
   `--unpaused` semantics.
4. Create a private temporary staging directory with `mktemp -d`, then fetch
   every candidate through:

   ```text
   datus mwaa [--profile ENV_PROFILE] dags source DAG_ID [--env MWAA_ENV]
   ```

5. Prefer `relative_fileloc`; otherwise sanitize `fileloc`, rejecting `..` and
   absolute traversal. Fall back to `DAG_ID.py` and resolve collisions.
6. Deduplicate shared files by normalized location and checksum. Never split a
   Python file defining multiple DAGs.
7. If any source call fails, list the failed DAGs and stop. Do not scan S3 or
   call an object-storage plugin to make the export appear complete.

## 2. Build and repeatedly refine the proposal

Default to full export. Accept natural-language filters by DAG id/glob/regex,
owner, tag, paused state, source path, include/exclude list, connection id, or
source keyword/regex. Static connection detection covers literal
`*_conn_id`/`conn_id`, `BaseHook.get_connection(...)`, and Jinja `conn.<id>`.
Mark dynamic connection values unresolved. State AND/OR semantics explicitly.

Show:

```text
MWAA profile/environment: <profile>/<environment>
Selection: full | selective
Rules: <normalized rules>
DAGs: <count and sorted ids>
Files: <count and sorted safe paths>
Shared files: <file -> associated DAGs>
Unresolved source/dynamic matches: <none or details>
Destination: <path or URI>
Transfer: local | datus s3 | datus gcs | datus adls
```

Ask the user to confirm this exact proposal. A correction or adjustment is not
confirmation. Any scope-affecting discovery after approval requires a new
proposal and confirmation.

## 3. Materialize after explicit confirmation

Write selected source files plus `dag-export-manifest.json` containing:

- contract `airflow-dag-export/v1`, plugin `mwaa`, profile/environment, UTC time;
- selection mode and normalized rules;
- DAG active/paused metadata;
- safe relative file paths, all associated DAG ids, and `sha256:<hex>`;
- summary `{total_dags, total_files, succeeded, failed}`.

### dag-export-manifest.json

```json
{
  "contract": "airflow-dag-export/v1",
  "plugin": "mwaa",
  "environment": "prod-mwaa",
  "exported_at": "2026-08-19T00:00:00Z",
  "selection": {"mode": "selective", "rules": ["connection_id == warehouse"]},
  "summary": {"total_dags": 1, "total_files": 1, "succeeded": 1, "failed": 0},
  "dags": [
    {"dag_id": "orders", "is_active": true, "is_paused": false, "file": "orders.py"}
  ],
  "files": [
    {"path": "orders.py", "dag_ids": ["orders"], "checksum": "sha256:<hex>"}
  ]
}
```

Do not include AWS credentials, web tokens, cookies, connection values, or
temporary paths.

## 4. Agent-driven destination upload

MWAA itself contains no S3 file transfer. Route by destination schema:

| Destination | Handler |
|---|---|
| local path or `file://` | filesystem copy |
| `s3://` | `datus s3 cp` / `datus s3 sync` |
| `gs://` | `datus gcs cp` / `datus gcs sync` |
| `abfs://`, `abfss://`, `adls://` | `datus adls cp` / `datus adls sync` |

Unknown schemes fail closed. The selected storage plugin owns its profile and
credentials; never reuse or expose the MWAA profile's secrets. Never invoke
`rm`, move, prune, or delete as part of export.

Verify uploaded files and checksums with the destination handler. For an MWAA
DAG prefix, poll `datus mwaa dags list` and use the Airflow import-error view
through the existing MWAA CLI only when needed. Clean temporary staging after
success or failure and retain only the confirmed output.
