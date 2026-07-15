# Datus Plugins

Official plugins for [**Datus**](https://github.com/Datus-ai/Datus-agent) — the
data-engineering agent. Each plugin wraps an SDK, REST API, or cloud service as a
`datus <command>` subcommand that both you and the agent can drive.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue.svg)](https://www.python.org/)
![Status](https://img.shields.io/badge/status-experimental-orange.svg)

A plugin is a plain Python distribution discovered through the `datus.plugins`
entry-point group. It **never imports `datus`** — the whole contract is a
declarative `datus-plugin.yml` manifest. Install only the ones you need; Datus
picks them up automatically.

## What's inside

Eleven plugins, each its own independently versioned distribution. All currently
ship at `0.1.x` — 🧪 **Experimental** (functional and contract-tested, but the
command surface and config schema may still change; see
[Versioning & maturity](#versioning--maturity)).

| Command | Distribution | Version | What it does | Docs |
|---|---|:---:|---|:---:|
| `datus airflow` | `datus-airflow-plugin` | `0.1.0` | Drive remote Apache Airflow 3.x over REST API v2, with DAG deploy to S3 or a dags folder | [↗](datus-airflow-plugin/README.md) |
| `datus statsig` | `datus-statsig-plugin` | `0.1.0` | Read Statsig metrics & experiment results, author warehouse-native metric SQL, drive ETL ingestion (Console API) | [↗](datus-statsig-plugin/README.md) |
| `datus s3` | `datus-s3-plugin` | `0.1.0` | Browse and move S3 data (ls/stat/cat/cp/sync/rm/presign) and run S3 Select SQL | [↗](datus-aws-plugins/datus-s3-plugin/README.md) |
| `datus glue` | `datus-glue-plugin` | `0.1.0` | Browse the Glue Data Catalog and run/monitor Glue crawlers and ETL jobs (with logs) | [↗](datus-aws-plugins/datus-glue-plugin/README.md) |
| `datus iam` | `datus-iam-plugin` | `0.1.0` | Read-only IAM inspection and permission simulation (the `AccessDenied` diagnostic) | [↗](datus-aws-plugins/datus-iam-plugin/README.md) |
| `datus emr` | `datus-emr-plugin` | `0.1.0` | Submit/monitor steps on existing EMR (on EC2) clusters and read step logs | [↗](datus-aws-plugins/datus-emr-plugin/README.md) |
| `datus emr-serverless` | `datus-emr-serverless-plugin` | `0.1.0` | Operate EMR Serverless applications and run/monitor Spark job runs | [↗](datus-aws-plugins/datus-emr-serverless-plugin/README.md) |
| `datus ecs` | `datus-ecs-plugin` | `0.1.0` | Run/monitor tasks on existing ECS/Fargate clusters, scale services, read task logs | [↗](datus-aws-plugins/datus-ecs-plugin/README.md) |
| `datus cloudwatch` | `datus-cloudwatch-plugin` | `0.1.0` | Query CloudWatch logs (incl. Logs Insights), metrics, alarms and dashboards | [↗](datus-aws-plugins/datus-cloudwatch-plugin/README.md) |
| `datus quicksight` | `datus-quicksight-plugin` | `0.1.0` | Browse QuickSight datasets/dashboards/analyses and refresh SPICE ingestions | [↗](datus-aws-plugins/datus-quicksight-plugin/README.md) |
| `datus mwaa` | `datus-mwaa-plugin` | `0.1.0` | Inspect MWAA environments, mint tokens, and run the Airflow CLI over REST | [↗](datus-aws-plugins/datus-mwaa-plugin/README.md) |

The nine AWS plugins share [`datus-aws-common`](datus-aws-plugins/datus-aws-common/README.md)
(boto3 session/AssumeRole, config, error mapping, output rendering) — an internal
library, not a plugin, installed automatically as a dependency.

## Requirements

- **[Datus agent](https://github.com/Datus-ai/Datus-agent)** installed — plugins
  run inside its interpreter.
- **Python ≥ 3.12**.
- Credentials for the service a plugin talks to (AWS profile/role, Statsig
  Console API key, Airflow token, …) — see each plugin's README.

## Quickstart

### 1. Install a plugin

```bash
# From PyPI (published distributions) — pip resolves shared deps like datus-aws-common
datus plugin install pip:datus-s3-plugin

# From a local checkout of this repo (development / unreleased)
datus plugin install src:./datus-aws-plugins/datus-s3-plugin
```

`datus plugin install` also accepts `git:<url>`, `whl:<wheel>`, and `zip:<bundle>`
sources. Verify it registered:

```bash
datus s3 --help
```

### 2. Configure a profile

Plugins read their config from profiles under `agent.plugins.<name>` in your
`agent.yml`. Secrets are **always** `${ENV_VAR}` references, never literals:

```yaml
agent:
  plugins:
    s3:
      prod:
        default: true                 # used when --profile is omitted
        region: us-east-1
        # AWS creds resolve via the standard chain, or set explicitly:
        # access_key_id: ${AWS_ACCESS_KEY_ID}
        # secret_access_key: ${AWS_SECRET_ACCESS_KEY}
```

### 3. Run

```bash
datus s3 ls s3://my-bucket/           # uses the default profile
datus s3 --profile staging cat s3://my-bucket/report.json
```

## Configuration

- **Profiles & environments.** Each plugin can define multiple named profiles
  under `agent.plugins.<name>`; mark one `default: true` and switch with
  `--profile <name>`.
- **Secrets.** Reference environment variables with `${VAR}` — never commit a
  literal key. Each plugin's README lists which fields are secret.
- **Output.** JSON by default; use `-o table|plain|yaml` for other formats and
  `--compact` for single-line JSON.

## Safety & permissions

- **Exit codes**: `0` success · `1` runtime/API error · `2` usage · `3` config
  error · `8` missing optional dependency.
- **Destructive commands prompt** for confirmation and accept `-y/--yes` to skip.
- **Agent-facing risk** is declared in each manifest's `permissions` key
  (`allow` / `ask` per permission profile), so the agent asks before running
  anything sensitive.

## Develop your own plugin

### Use the development skill (recommended)

`datus-plugin-development` is a bundled agent skill that turns any SDK / REST API
/ documentation into an installable plugin. It is **design-first**: it produces a
design draft (config schema, command list with doc citations + permission rules,
bundled-skill plan) and **stops for your confirmation** before writing any code.
The full plugin contract is inlined in the skill, so it needs no external docs.

#### Claude Code

```
/plugin marketplace add Datus-ai/Datus-Plugins
/plugin install datus-plugin-development@datus-plugin
```

Then invoke it with the SDK / API / docs to wrap:

```
/datus-plugin-development <path or URL to the SDK / API / docs, plus the desired command name>
```

#### Codex

```bash
codex plugin marketplace add Datus-ai/Datus-Plugins
```

Open the plugin browser with `/plugins` and install **datus-plugin-development**,
then start a new session and invoke the skill:

```
$datus-plugin-development <path or URL to the SDK / API / docs, plus the desired command name>
```

### The plugin contract

Three rules keep every plugin clean:

- **No `datus` import.** The whole contract is the declarative `datus-plugin.yml`
  manifest inside the import package; `tests/test_plugin_contract.py` pins it down.
- **No cross-plugin imports.** Plugins never import each other. Shared code is
  extracted into a dedicated library distribution (like `datus-aws-common`) only
  once several plugins need it.
- **One distribution, one plugin.** Each distribution declares exactly one
  `datus.plugins` entry point.

A standalone plugin follows the naming triple:

```
datus-<name>-plugin/            # directory & PyPI distribution name
├── pyproject.toml              # [project.entry-points."datus.plugins"] <name> = "datus_<name>_plugin"
├── README.md
├── datus_<name>_plugin/        # import package
│   ├── datus-plugin.yml        # the declarative manifest (the contract)
│   ├── prompts/                # system-prompt Jinja2 template (system.md.j2)
│   ├── cli/                    # one module per command group, each exposing register(sub)
│   └── skills/                 # bundled agent skills (SKILL.md per skill)
└── tests/
    └── test_plugin_contract.py # manifest conformance tests
```

### Local install & packaging

```bash
datus plugin install src:./datus-<name>-plugin   # installs into ~/.datus/plugins/<name>/
datus plugin pack -o ./dist                       # build a distributable wheelhouse .zip
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workspace setup, the manifest
reference, and the release process.

## Versioning & maturity

Every distribution follows [SemVer](https://semver.org) and is versioned
independently. Maturity is tracked with two states:

- **🧪 Experimental (`0.1.x`)** — the current default for every distribution.
  Functional and covered by contract tests, but **not yet production-validated**:
  the command surface, profile schema, and permission posture may still change
  without a major bump while below `1.0`.
- **✅ Stable (`1.0.0`+)** — promoted only after real-world usage. From `1.0.0`
  on, the CLI and profile schema carry SemVer compatibility guarantees; breaking
  changes require a major version bump.

A distribution graduates by bumping its version to `1.0.0` in its
`pyproject.toml`. Each plugin (and `datus-aws-common`) is promoted independently.

## Contributing

Contributions are welcome — new plugins, fixes, and docs. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow (uv workspace,
tests, branching) and the plugin contract in depth.

## License

[Apache-2.0](LICENSE) © Datus-ai.
