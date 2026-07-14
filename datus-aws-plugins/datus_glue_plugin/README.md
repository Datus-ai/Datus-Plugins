# datus-glue-plugin

A [Datus](https://datus.ai) plugin that drives the **AWS Glue** Data Catalog,
crawlers and ETL jobs from `datus glue ...` — browse schemas, run and monitor
crawlers and jobs, and read job logs. Backed by boto3.

```bash
pip install datus-aws-plugins
```

## Configuration

Profiles live under `agent.plugins.glue.<profile>` in Datus' `agent.yml`:

```yaml
agent:
  plugins:
    glue:
      prod:
        default: true
        region: us-east-1
        catalog_id: "123456789012"   # optional cross-account Data Catalog id
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Group | Subcommands |
|---|---|
| `catalog` | `databases`, `tables`, `show` (schema), `search`, `partitions`, `versions`, `stats`, and `create/update/delete` of databases/tables/partitions |
| `crawlers` | `list`, `get`, `status`, `run [--wait]`, `stop`, `history`, `metrics`, `schedule-pause/resume` |
| `jobs` | `list`, `get`, `run [--wait]`, `run-status`, `runs`, `stop`, `logs`, `bookmark-reset` |
| `connections` | `list`, `get` (secrets masked) |

```bash
datus glue catalog show sales orders
datus glue crawlers run raw_ingest --wait
datus glue jobs run daily_etl --args '{"--env":"prod"}' --wait
datus glue jobs logs daily_etl jr_abc123 --error
```

Crawler runs only update the catalog (routine); **job runs write data and are
billed** (always confirmed). `catalog show` renders columns, partition keys,
location and format; `jobs logs` reads the run's CloudWatch logs.

## Exit codes

`0` success · `1` runtime/API error (also: failed run/crawl under `--wait`) ·
`2` usage · `3` config error.

## Development

```bash
uv run --package datus-glue-plugin pytest datus-glue-plugin
```

Never imports `datus`; implements the plugin contract and registers the `glue`
entry point in `datus.plugins`. Shared boto3 plumbing lives in
`datus-aws-common`. Bundled skills: `glue` and `glue-setup`.

## Agent bash permissions

Catalog reads and crawler/job inspection run everywhere; crawler run/stop,
schedule changes, and job stop/bookmark-reset are routine (confirmed under
`normal`, auto under `auto`); `jobs run` and every catalog mutation always
require confirmation. User rules in `agent.yml` always win.
