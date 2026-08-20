---
name: airflow-dag-export
description: Export current Airflow DAG Python sources with an adjustable, explicitly confirmed scope, then optionally upload them through the storage plugin selected by the destination URI
---

# Airflow DAG Export

Use this skill when a user asks to export, copy, migrate, back up, or deploy
DAG Python files from a configured `datus airflow` environment. The workflow
is LLM-driven: there is no export-plan or export CLI command.

## Non-negotiable rules

- The live Airflow API is the only authority for the candidate DAG set and
  source contents. Never list or scan `dags_folder` to discover candidates.
- The default scope is every active, non-stale DAG returned by
  `datus airflow dags list -o json`. Paused DAGs are active and included.
- Retrieve Python through `datus airflow dags source <dag_id>`. Do not rebuild
  source from serialized DAG JSON and do not silently fall back to object
  storage when an API source call fails.
- A temporary staging directory may be created for read-only inspection, but
  do not write the requested export destination or upload anything until the
  user explicitly confirms the exact current proposal.
- Scope adjustments are never confirmation. Recompute and show the complete
  proposal after every adjustment, then wait again.
- Never prune or delete destination files as part of export/upload.

## 1. Discover and stage from the API

1. Select the Airflow profile requested by the user. Preserve
   `dag_id_prefix`; it is an environment guardrail and may narrow API output.
2. Run the unbounded JSON listing (do not add `--limit`):

   ```text
   datus airflow [--profile ENV] dags list -o json
   ```

3. Verify every candidate is active/non-stale. Do not exclude paused DAGs
   unless the user explicitly asks for unpaused DAGs only.
4. Create a private temporary staging directory with `mktemp -d`. For each
   candidate DAG, call:

   ```text
   datus airflow [--profile ENV] dags source DAG_ID
   ```

5. Use `relative_fileloc` when the API supplies it. Otherwise derive a safe
   relative name from `fileloc`; reject absolute traversal and `..`. When no
   safe file path exists, use `DAG_ID.py`. Resolve path collisions explicitly.
6. Deduplicate a file shared by several DAGs using normalized file location,
   then source checksum. Never split a Python file that defines multiple DAGs.
7. If any candidate's source cannot be fetched, report the failed DAG IDs and
   stop. Do not describe the result as a complete export and do not inspect
   `dags_folder` to fill the gap.

## 2. Build an adjustable proposal

Start with the full candidate set. Natural-language filters may use:

- DAG id, exact value, glob, or regex;
- owner, tag, paused state, or source file path;
- include/exclude lists;
- source keyword or regex;
- connection id discovered statically in `*_conn_id=...`, `conn_id=...`,
  `BaseHook.get_connection(...)`, or Jinja `conn.<id>` references.

Connection and keyword matching is static analysis. Mark dynamic values as
unresolved rather than guessing. Combine rules exactly as requested and state
whether each combination is AND or OR.

Show a proposal containing:

```text
Environment: <profile>
Selection: full | selective
Rules: <normalized include/exclude expression>
DAGs: <count and sorted ids>
Files: <count and sorted safe relative paths>
Shared files: <file -> every DAG defined by it>
Unresolved source/dynamic matches: <none or details>
Destination: <local path or URI>
Transfer: local | datus s3 | datus gcs | datus adls
```

Ask for explicit confirmation of this exact proposal. If the user changes a
filter, destination, file name, or inclusion rule, recompute the proposal and
ask again. A question, correction, or request to inspect more details is not
approval. If discovery changes after approval, invalidate approval and ask
again.

## 3. Materialize only after confirmation

Write only the selected, deduplicated Python files. Alongside them write
`dag-export-manifest.json` with:

- contract `airflow-dag-export/v1` and plugin `airflow`;
- environment, UTC export time, and `selection.mode`/rules;
- every DAG id and its active/paused metadata;
- every file's relative path, associated DAG ids, and `sha256:<hex>` checksum;
- summary `{total_dags, total_files, succeeded, failed}`.

### dag-export-manifest.json

```json
{
  "contract": "airflow-dag-export/v1",
  "plugin": "airflow",
  "environment": "prod",
  "exported_at": "2026-08-19T00:00:00Z",
  "selection": {"mode": "full", "rules": []},
  "summary": {"total_dags": 2, "total_files": 1, "succeeded": 1, "failed": 0},
  "dags": [
    {"dag_id": "orders", "is_active": true, "is_paused": false, "file": "team/dags.py"},
    {"dag_id": "customers", "is_active": true, "is_paused": true, "file": "team/dags.py"}
  ],
  "files": [
    {"path": "team/dags.py", "dag_ids": ["customers", "orders"], "checksum": "sha256:<hex>"}
  ]
}
```

Never put credentials, tokens, connection values, or temporary paths in the
manifest.

## 4. Route an optional upload by destination schema

The agent performs the upload; Airflow contains no storage client:

| Destination | Handler |
|---|---|
| local path or `file://` | filesystem copy (`file://` becomes a local path) |
| `s3://` | `datus s3 cp` for files or `datus s3 sync` for the selected directory |
| `gs://` | `datus gcs cp` or `datus gcs sync` |
| `abfs://`, `abfss://`, `adls://` | `datus adls cp` or `datus adls sync` |

For an unknown scheme, stop and name the missing storage capability. Do not
reinterpret it as S3. Storage plugins own credentials and may perform their
own permission confirmation. Do not pass storage credentials through the
Airflow profile.

After upload, verify object/file presence and checksum with filesystem tools
or the selected storage plugin's `stat`/`cat`. If the destination is an
Airflow DAG root, poll `dags list/details` and `dags list-import-errors` until
the expected DAGs are parsed or a bounded timeout expires.

Always clean the temporary staging directory after success or failure; keep
the confirmed destination and its manifest.
