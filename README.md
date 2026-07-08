# Datus-Plugins

Monorepo for [Datus](https://datus.ai) plugins. Each plugin lives in its own
top-level directory, is versioned and published to PyPI independently, and
never imports `datus` — it only implements the plugin contract and registers
an entry point in the `datus.plugins` group.

| Plugin | Command | Description |
|---|---|---|
| [datus-airflow-plugin](datus-airflow-plugin/) | `datus airflow` | Drive remote Apache Airflow 3.x over the REST API v2, with DAG deploy to S3 or a dags folder |
| [datus-glue-plugin](datus-glue-plugin/) | `datus glue` | Browse the Glue Data Catalog and run/monitor Glue crawlers and ETL jobs (with logs) |
| [datus-s3-plugin](datus-s3-plugin/) | `datus s3` | Browse and move S3 data (ls/stat/cat/cp/sync/rm/presign) and run S3 Select SQL |
| [datus-cloudwatch-plugin](datus-cloudwatch-plugin/) | `datus cloudwatch` | Query CloudWatch logs (incl. Logs Insights), metrics, alarms and dashboards |
| [datus-emr-plugin](datus-emr-plugin/) | `datus emr` | Submit/monitor steps on existing EMR (on EC2) clusters and read step logs |
| [datus-emr-serverless-plugin](datus-emr-serverless-plugin/) | `datus emr-serverless` | Operate EMR Serverless applications and run/monitor Spark job runs |
| [datus-ecs-plugin](datus-ecs-plugin/) | `datus ecs` | Run/monitor tasks on existing ECS/Fargate clusters, scale services, read task logs |
| [datus-quicksight-plugin](datus-quicksight-plugin/) | `datus quicksight` | Browse QuickSight datasets/dashboards/analyses and refresh SPICE ingestions |
| [datus-iam-plugin](datus-iam-plugin/) | `datus iam` | Read-only IAM inspection and permission simulation (the `AccessDenied` diagnostic) |
| [datus-mwaa-plugin](datus-mwaa-plugin/) | `datus mwaa` | Inspect MWAA environments, mint tokens, and run the Airflow CLI over REST |

AWS plugins share [datus-aws-common](datus-aws-common/) (boto3 session/AssumeRole, config, error mapping, output rendering, CLI helpers).

## Layout & conventions

Every plugin follows the same naming triple:

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

Rules that keep plugins independently installable:

- **No `datus` import, no cross-plugin imports.** The contract is duck-typed;
  `tests/test_plugin_contract.py` is what pins it down — copy it into new
  plugins.
- **Prefer copying small helpers** (`output.py`, `errors.py`, `config.py`
  patterns) over extracting a shared library, until at least three plugins
  need the same code.
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
```

Starting a new plugin: copy the structure above (datus-airflow-plugin is the
reference implementation), pick `<name>`, and it is picked up by the
workspace automatically via the `datus-*-plugin` member glob.

## Releases

Plugins are tagged and released independently:
`datus-<name>-plugin/v<version>` (e.g. `datus-airflow-plugin/v0.1.0`).
