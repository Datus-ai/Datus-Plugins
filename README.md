# Datus-Plugins

Monorepo for [Datus](https://datus.ai) plugins. A plugin never imports `datus` —
it implements the duck-typed plugin contract and registers an entry point in the
`datus.plugins` group. Every plugin is its **own distribution**:
`datus-airflow-plugin` and `datus-statsig-plugin` stand alone at the repo root,
while the nine AWS plugins live under [`datus-aws-plugins/`](datus-aws-plugins/),
each depending on the shared `datus-aws-common` distribution in that directory.

All distributions currently ship at `0.1.x` (🧪 Experimental) — see
[Versioning & maturity](#versioning--maturity).

| Command | Distribution | Description |
|---|---|---|
| [`datus airflow`](datus-airflow-plugin/) | `datus-airflow-plugin` | Drive remote Apache Airflow 3.x over the REST API v2, with DAG deploy to S3 or a dags folder |
| [`datus statsig`](datus-statsig-plugin/) | `datus-statsig-plugin` | Read Statsig metrics & experiment results, author warehouse-native metric SQL, and drive ETL ingestion (Console API) |
| [`datus s3`](datus-aws-plugins/datus-s3-plugin/README.md) | `datus-s3-plugin` | Browse and move S3 data (ls/stat/cat/cp/sync/rm/presign) and run S3 Select SQL |
| [`datus glue`](datus-aws-plugins/datus-glue-plugin/README.md) | `datus-glue-plugin` | Browse the Glue Data Catalog and run/monitor Glue crawlers and ETL jobs (with logs) |
| [`datus iam`](datus-aws-plugins/datus-iam-plugin/README.md) | `datus-iam-plugin` | Read-only IAM inspection and permission simulation (the `AccessDenied` diagnostic) |
| [`datus emr`](datus-aws-plugins/datus-emr-plugin/README.md) | `datus-emr-plugin` | Submit/monitor steps on existing EMR (on EC2) clusters and read step logs |
| [`datus emr-serverless`](datus-aws-plugins/datus-emr-serverless-plugin/README.md) | `datus-emr-serverless-plugin` | Operate EMR Serverless applications and run/monitor Spark job runs |
| [`datus ecs`](datus-aws-plugins/datus-ecs-plugin/README.md) | `datus-ecs-plugin` | Run/monitor tasks on existing ECS/Fargate clusters, scale services, read task logs |
| [`datus cloudwatch`](datus-aws-plugins/datus-cloudwatch-plugin/README.md) | `datus-cloudwatch-plugin` | Query CloudWatch logs (incl. Logs Insights), metrics, alarms and dashboards |
| [`datus quicksight`](datus-aws-plugins/datus-quicksight-plugin/README.md) | `datus-quicksight-plugin` | Browse QuickSight datasets/dashboards/analyses and refresh SPICE ingestions |
| [`datus mwaa`](datus-aws-plugins/datus-mwaa-plugin/README.md) | `datus-mwaa-plugin` | Inspect MWAA environments, mint tokens, and run the Airflow CLI over REST |

The AWS plugins share `datus-aws-common` (boto3 session/AssumeRole, config, error
mapping, output rendering, CLI helpers) — a shared **internal** library, not a
plugin, published as its own distribution under
[`datus-aws-plugins/`](datus-aws-plugins/).

## Layout & conventions

A standalone plugin follows the naming triple:

```
datus-<name>-plugin/            # directory & PyPI distribution name
├── pyproject.toml              # entry point: [project.entry-points."datus.plugins"] <name> = ...
├── README.md
├── datus_<name>_plugin/        # import package
│   ├── plugin.py               # contract: run_cli / skills_dir / system_prompt / cli_permissions
│   ├── cli/                    # one module per command group, each exposing register(sub)
│   └── skills/                 # bundled agent skills (SKILL.md per skill)
└── tests/
    └── test_plugin_contract.py # duck-typed contract conformance tests
```

The nine AWS plugins follow the same triple but are grouped under
[`datus-aws-plugins/`](datus-aws-plugins/) — one `datus-<service>-plugin/`
distribution each, all depending on the sibling `datus-aws-common/` distribution
(the shared `datus_aws_common/` import package). See its
[README](datus-aws-plugins/README.md).

Rules that keep the plugin contract clean:

- **No `datus` import.** The contract is duck-typed; `tests/test_plugin_contract.py`
  is what pins it down — copy it into new plugins.
- **No cross-plugin imports.** Plugins never import each other. Shared code is
  extracted into a dedicated library (`datus_aws_common`) only once several plugins
  need it — until then, prefer copying small helpers (`output.py`, `errors.py`,
  `config.py` patterns).
- **Exit codes**: `0` success · `1` runtime/API error · `2` usage · `3` config
  error · `8` missing optional dependency.
- **Destructive commands prompt** and accept `-y/--yes`; agent-facing risk is
  declared via `cli_permissions()` (allow / ask per permission profile).

## Development

This is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/):
one lockfile and one `.venv` at the root cover all members.

```bash
uv sync --all-extras                                  # set up everything
uv run --package datus-airflow-plugin pytest datus-airflow-plugin
uv run --package datus-s3-plugin pytest datus-aws-plugins/datus-s3-plugin
```

Starting a new standalone plugin: copy the structure above (datus-airflow-plugin
is the reference implementation), pick `<name>`, and it is picked up by the
workspace automatically via the `datus-*-plugin` member glob. A new AWS plugin
goes into `datus-aws-plugins/` as another `datus-<service>-plugin/` distribution
that depends on `datus-aws-common`; it is picked up by the
`datus-aws-plugins/datus-*-plugin` member glob.

## Versioning & maturity

Every distribution follows [SemVer](https://semver.org) and is versioned
independently. Maturity is tracked with two states:

- **🧪 Experimental (`0.1.x`)** — the current default for every distribution.
  Functional and covered by contract tests, but **not yet production-validated**:
  the command surface, profile schema, and permission posture may still change
  without a major bump while below `1.0`.
- **✅ Stable (`1.0.0`+)** — promoted only after real-world usage has shaken the
  distribution out. From `1.0.0` on, its CLI and profile schema carry SemVer
  compatibility guarantees; breaking changes require a major version bump.

A distribution graduates from Experimental to Stable by bumping its version to
`1.0.0` in its `pyproject.toml`. Each AWS plugin (and `datus-aws-common`) is
promoted independently.

## Releases

Distributions are tagged and released independently:
`<distribution>/v<version>` (e.g. `datus-airflow-plugin/v0.1.0`,
`datus-s3-plugin/v0.1.0`). Each AWS plugin is versioned and released on its own;
bumping `datus-aws-common` may require dependent plugins to widen their version
constraint.
