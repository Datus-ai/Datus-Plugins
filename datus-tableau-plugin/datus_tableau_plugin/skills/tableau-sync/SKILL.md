---
name: tableau-sync
description: Synchronize selected or all Tableau dashboards with chart metadata, dataset definitions, source SQL and ETL dependencies into a checked local source bundle
---

# Synchronize Tableau Assets

First confirm the site: `datus tableau status` must report `site_configured`
true, or every command below needs `--site CONTENT_URL` (see the `tableau`
skill for choosing a site with `sites list`).

Then resolve the selection. If the user already named a dashboard or project,
confirm it against discovery output. If they did not, walk them through a
choice instead of guessing:

1. `datus tableau projects list` — present `name` and `id` and ask which
   project.
2. `datus tableau dashboards list --project-id PROJECT_ID` — present each
   dashboard's `name`, `id` and `workbook_id` and ask which dashboard(s), or
   whether they want the whole project.
3. Only use `--all` when the user explicitly asks for the entire site.

Do not widen a single dashboard to a project or site-wide export, and do not
start `sync run` before the user has confirmed the selection.

### Commands

```bash
datus tableau --profile prod sync run --dashboard-id DASHBOARD_ID
datus tableau --profile prod sync run --project-id PROJECT_LUID
datus tableau --profile prod sync run --all
datus tableau sync inspect bi_sources/tableau/BUNDLE --summary
```

Use exactly one selection mode. Repeat the ID flag for multiple selections.
Inspect `sync run --help` for output and existing-bundle options. Default output
is beneath `bi_sources/tableau/` in the project; an existing snapshot must not
be replaced without an explicit `--overwrite` decision.

Read the command's returned output directory rather than guessing its slug.
Inspect that bundle offline with `sync inspect` before consuming any files.
Start with `sync inspect PATH --summary` for a bounded JSON overview. It retains
full file/schema/identity/dependency validation, and reports omitted/truncated
items. Use the full response or verified files when an exact identity or missing
item is needed; a clipped chart ID cannot serve as a source binding.
Verify the source identity, requested selections, checksums, dependencies,
coverage and errors. An existing bundle failing checksum checks is not usable
context. Do not rewrite its checksums to make it pass.

Check every selected dashboard for its worksheet/chart references, layouts,
filters, parameters, datasets and upstream flows. Shared datasets and transforms
must use the same source identity. Keep REST LUIDs, Metadata IDs and workbook
internal identifiers distinct, including identically named sheets in different
workbooks. Missing permissions or a GraphQL partial response are not evidence
that a dependency does not exist.

For a downloaded Prep flow without verified dashboard lineage, plan it offline
with `datus tableau plan --flow FLOW --flow-id ID --output DIR`.
Use the managed Tableau CLI runtime in SaaS; this offline path does not
authenticate a Tableau profile.
Inspect `pipeline.json.source_bindings_required` and local semantic diagnostics.
Use the README's source-binding contract to map full source transform IDs to
actual target tables; optional columns validate the exact native order. Pass
`--source-bindings FILE` to the planner or `sync run`. Keep retrieved source
assets immutable and use binding provenance in derived artifacts. A local
synthetic fixture can test SQL behavior; label that evidence separately from
real source data or warehouse validation. A partial plan remains partial when
DATE string parsing, missing pivot fields, union types or provenance are
unresolved, even if independent branches generate models.

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
not business extracts. For requested reference SQL/metrics or Datus dashboard output, return the
verified selection to `dashboard-bootstrap`, loading `tableau-rebuild` as platform
guidance. The common workflow owns metric-before-visual ordering; the plugin owns
DOSI binding/preflight and SQL-first processing contracts. A standalone sync stops
after its source report. Synchronization does not by itself
prove numerical correctness or completed SaaS migration.
