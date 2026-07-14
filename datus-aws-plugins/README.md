# datus-aws-plugins

The [Datus](https://datus.ai) **AWS plugin suite** — nine AWS plugins shipped as
a single distribution over one shared boto3/session/output layer
(`datus_aws_common`). Installing this one package registers all nine
`datus.plugins` entry points at once.

```bash
pip install datus-aws-plugins
```

Each plugin is still discovered and driven independently by the datus host via
its own entry point and `datus <command>` — bundling only changes how the code
is *packaged and released*, not how it is *used*.

## Plugins in this package

| Command | Import package | Docs |
|---|---|---|
| `datus s3` | `datus_s3_plugin` | [README](datus_s3_plugin/README.md) |
| `datus glue` | `datus_glue_plugin` | [README](datus_glue_plugin/README.md) |
| `datus iam` | `datus_iam_plugin` | [README](datus_iam_plugin/README.md) |
| `datus emr` | `datus_emr_plugin` | [README](datus_emr_plugin/README.md) |
| `datus emr-serverless` | `datus_emr_serverless_plugin` | [README](datus_emr_serverless_plugin/README.md) |
| `datus ecs` | `datus_ecs_plugin` | [README](datus_ecs_plugin/README.md) |
| `datus cloudwatch` | `datus_cloudwatch_plugin` | [README](datus_cloudwatch_plugin/README.md) |
| `datus quicksight` | `datus_quicksight_plugin` | [README](datus_quicksight_plugin/README.md) |
| `datus mwaa` | `datus_mwaa_plugin` | [README](datus_mwaa_plugin/README.md) |

`datus_aws_common` (boto3 session/AssumeRole, config, error mapping, output
rendering, CLI helpers) is the shared **internal** library — it registers no
entry point and is no longer published on its own. See its
[README](datus_aws_common/README.md).

## Layout

Ten top-level import packages ship in one wheel:

```
datus-aws-plugins/
├── pyproject.toml                 # one [project], nine datus.plugins entry points
├── datus_aws_common/              # shared internal library (no entry point)
├── datus_<service>_plugin/        # one import package per service
│   ├── plugin.py                  # contract: run_cli / skills_dir / system_prompt / cli_permissions
│   ├── cli/                       # one module per command group
│   └── skills/                    # bundled agent skills (SKILL.md per skill)
└── tests/
    └── <service>/                 # per-service test dir (contract + command tests)
```

## Development

Part of the root [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/):

```bash
uv sync --all-extras
uv run --package datus-aws-plugins pytest datus-aws-plugins
```

Tests run under `--import-mode=importlib` so each per-service directory can
reuse the same `conftest.py` / `test_commands.py` / `test_plugin_contract.py`
file names without collisions.
