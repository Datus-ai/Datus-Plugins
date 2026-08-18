---
name: superset
description: Operate Apache Superset dashboards, charts, datasets, SQL Lab, reports, tags, annotations, RLS, cache, and migration through `datus superset`
---

# Superset

Use this skill for analysis and BI delivery on a configured Superset instance.

## Workflow

1. Check connectivity with `datus superset status health`.
2. Discover before changing: use `dashboards list`, `charts list`, `datasets list`, and `databases list`; pass repeated `--param KEY=VALUE` for official API filters such as `q`.
3. Retrieve the current object before an update. Put request bodies in a project-local JSON file and use `--json-file`; keep secrets out of files and output.
4. Treat create/update/delete, SQL execution, cache, import/export, and `api call` as confirmation-requiring operations.
5. Verify writes with the corresponding `get` or `list` call.

## Where to run a query

Superset is the source of truth for BI semantics, not for data. Resolve each chart query's Dataset and Database independently; one Superset instance or dashboard may use multiple data connections. Dashboard SQL exports carry a credential-free `source_identity` per query for matching against Datus datasources.

- **Datus datasource** — checking that SQL is correct, that tables and columns exist, that results look right, and any iteration on a query. Running these through Superset adds a hop, counts against its rate limit, and `sql-lab execute` additionally writes a row to Superset's query history.
- **Superset** — anything whose answer *is* Superset's behaviour: `charts query` and `charts data` (how Superset compiles form_data into SQL), `databases table-metadata` (the column types Superset will assign a dataset), `select-star` (its dialect-qualified naming), and `context export-dashboard`.

`databases validate-sql` is a syntax check only, offered for PostgreSQL and Presto and answering 422 elsewhere. It accepts SQL that names tables and columns that do not exist, so never treat an empty result as proof a query will run.

Use `datus superset <group> -h` and `datus superset <group> <command> -h` for exact arguments. Prefer typed commands. Use `api call METHOD /api/v1/...` only for official endpoints that lack a typed command; absolute URLs and `..` paths are rejected.

For reusable project SQL context, load `superset-query-export`. For dashboard construction, load `superset-dashboard-authoring`.
