# datus-statsig-plugin

A [Datus](https://github.com/) plugin that wraps the **Statsig Console API**
(version `20240601`) as `datus statsig`. It focuses on the data-analysis surface
of the API: reading metrics and experiment results, authoring warehouse-native
metric SQL, and driving ETL ingestion — not feature-flag / project management.

## Install

```bash
pip install -e datus-statsig-plugin
datus statsig --help
```

## Configure

Add a profile under `agent.plugins.statsig` in your `agent.yml`:

```yaml
agent:
  plugins:
    statsig:
      prod:
        default: true
        api_key: ${STATSIG_CONSOLE_API_KEY}   # secret — env var reference, never a literal
        # optional:
        api_base_url: https://statsigapi.net   # Console API root (plugin appends /console/v1)
        api_version: "20240601"
        timeout: 30
```

Create a **Console** API key at `console.statsig.com/api_keys`. Or run the
bundled `statsig-setup` skill for a guided setup.

| Field | Required | Secret | Default |
|-------|----------|--------|---------|
| `api_key` | yes | yes (`${VAR}`) | — |
| `api_base_url` | no | no | `https://statsigapi.net` |
| `api_version` | no | no | `20240601` |
| `timeout` | no | no | `30` |

## Commands

```
datus statsig [--profile <env>] <group> <subcommand> [args...]
```

| Group | Subcommands | Posture |
|-------|-------------|---------|
| `metrics` | list, get, sql, values / create, update, reload | reads allow · writes ask |
| `metric-source` | list, get / create, update, delete | reads allow · writes ask |
| `experiments` | list, get, pulse, summary, exposures, pulse-status / load-pulse | reads allow · writes ask |
| `ingestion` | get, runs, run, status, schedule-get / backfill, schedule-set | reads allow · writes ask |
| `warehouse-connections` | update | ask |
| `events` | list, get | allow |
| `logs` | query | allow |
| `reports` | get | allow |
| `describe` | `<group> <subcommand>` | allow (prints a write command's body template) |

Output is JSON by default (`--compact` for one line; `-o table|plain|yaml`).
Write commands take their body via `--json '<inline>'` or `--json-file <path>`
(`warehouse-connections update` accepts only `--json-file`, since it carries
credentials). Run `datus statsig describe <group> <subcommand>` — or add
`--help` — to see the body template; `--dry-run` validates without persisting.

**Rate limits:** mutations are capped at ~100/10s and ~900/15min per project.

## Permissions

When the agent runs these commands through its bash tool, read-only commands
(and `describe`) auto-run; every mutation confirms under both the `normal` and
`auto` permission profiles (re-running a write costs warehouse compute and
consumes the mutation rate limit).

## Bundled skills

- `statsig` — how to use the CLI for metric/experiment analysis and SQL authoring.
- `statsig-setup` — configure an environment profile.

## Development

```bash
pip install -e 'datus-statsig-plugin[dev]'
pytest datus-statsig-plugin
```

The plugin never imports `datus` — the whole contract is the declarative
`datus_statsig_plugin/datus-plugin.yml` manifest (CLI entry function,
permissions, config schema, system-prompt template, bundled skills). Datus is
the config broker: it reads the manifest without importing the package and
calls the declared `cli` function (`datus_statsig_plugin.cli:main`) with the
resolved profile dict.
