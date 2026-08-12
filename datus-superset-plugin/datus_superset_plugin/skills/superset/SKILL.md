---
name: superset
description: Operate Apache Superset dashboards, charts, datasets, SQL Lab, reports, tags, annotations, RLS, cache, and migration through `datus superset`
---

# Superset

Use this skill for analysis and BI delivery on a configured Superset instance.

## Workflow

1. Check connectivity with `datus superset status health` and identity with `datus superset status whoami`.
2. Discover before changing: use `dashboards list`, `charts list`, `datasets list`, and `databases list`; pass repeated `--param KEY=VALUE` for official API filters such as `q`.
3. Retrieve the current object before an update. Put request bodies in a project-local JSON file and use `--json-file`; keep secrets out of files and output.
4. Treat create/update/delete, SQL execution, cache, import/export, and `api call` as confirmation-requiring operations.
5. Verify writes with the corresponding `get` or `list` call.

Use `datus superset <group> -h` and `datus superset <group> <command> -h` for exact arguments. Prefer typed commands. Use `api call METHOD /api/v1/...` only for official endpoints that lack a typed command; absolute URLs and `..` paths are rejected.

For reusable project SQL context, load `superset-query-export`. For dashboard construction, load `superset-dashboard-authoring`.
