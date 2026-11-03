---
name: tableau-sync
description: Synchronize selected or all Tableau dashboards with chart metadata, dataset definitions, source SQL and ETL dependencies into a checked local source bundle
---

# Synchronize Tableau Assets

First discover the configured profile and dashboard identifiers using
`datus tableau dashboards list`. Confirm the user's intended selection from
their request; do not widen a single dashboard to a site-wide export.

### Commands

```bash
datus tableau --profile prod sync run --dashboard-id DASHBOARD_ID
datus tableau --profile prod sync run --project-id PROJECT_LUID
datus tableau --profile prod sync run --all
datus tableau sync inspect bi_sources/tableau/BUNDLE
```

Use exactly one selection mode. Repeat the ID flag for multiple selections.
Inspect `sync run --help` for output and existing-bundle options. Default output
is beneath `bi_sources/tableau/` in the project; an existing snapshot must not
be replaced without an explicit `--overwrite` decision.

Read the command's returned output directory rather than guessing its slug.
Inspect that bundle offline with `sync inspect` before consuming any files.
Verify the source identity, requested selections, checksums, dependencies,
coverage and errors. An existing bundle failing checksum checks is not usable
context. Do not rewrite its checksums to make it pass.

Check every selected dashboard for its worksheet/chart references, layouts,
filters, parameters, datasets and upstream flows. Shared datasets and transforms
must use the same source identity. Keep REST LUIDs, Metadata IDs and workbook
internal identifiers distinct, including identically named sheets in different
workbooks. Missing permissions or a GraphQL partial response are not evidence
that a dependency does not exist.

SQL retains its provenance: a source query is retrieved evidence; a derived
query is a reconstruction; target SQL is compiled for the chosen target model.
LOD expressions, table calculations and Prep operations must retain their
native semantics or an explicit unsupported status. Do not replace unavailable
chart SQL with plausible SQL and label it as original.

For external dataset processing, acquire definitions through configured Datus
datasource metadata tools or user-provided local SQL/source packages. A table
reference alone does not include the job that creates it. Record unresolved
upstream definitions and stop only the affected downstream rebuilds. Do not
execute downloaded scripts, apply DDL or run pipeline jobs.

Report successful, partial and failed objects separately, including unsupported
semantics and remaining ETL dependencies. Source packages include definitions,
not business extracts. Use `tableau-rebuild` for DOSI mapping, SQL-first processing
artifacts and datus-saas dashboard generation. Synchronization does not by itself
prove numerical correctness or completed SaaS migration.
