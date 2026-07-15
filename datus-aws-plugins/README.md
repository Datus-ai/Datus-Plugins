# datus-aws-plugins

The [Datus](https://datus.ai) **AWS plugins** — nine independently published
plugins plus the shared `datus-aws-common` library, grouped under this one
directory. Unlike `datus-airflow-plugin` and `datus-statsig-plugin` (which sit at
the repo root), the AWS plugins are collected here because they all build on the
same boto3/session/output layer — but each is still its **own distribution** with
its own `pyproject.toml`, version, and `datus.plugins` entry point.

Install only the plugins you need:

```bash
pip install datus-s3-plugin        # pulls in datus-aws-common
pip install datus-glue-plugin
```

Each plugin depends on `datus-aws-common`, so it is installed automatically.

## Distributions in this directory

| Command | Distribution | Import package | Docs |
|---|---|---|---|
| `datus s3` | `datus-s3-plugin` | `datus_s3_plugin` | [README](datus-s3-plugin/README.md) |
| `datus glue` | `datus-glue-plugin` | `datus_glue_plugin` | [README](datus-glue-plugin/README.md) |
| `datus iam` | `datus-iam-plugin` | `datus_iam_plugin` | [README](datus-iam-plugin/README.md) |
| `datus emr` | `datus-emr-plugin` | `datus_emr_plugin` | [README](datus-emr-plugin/README.md) |
| `datus emr-serverless` | `datus-emr-serverless-plugin` | `datus_emr_serverless_plugin` | [README](datus-emr-serverless-plugin/README.md) |
| `datus ecs` | `datus-ecs-plugin` | `datus_ecs_plugin` | [README](datus-ecs-plugin/README.md) |
| `datus cloudwatch` | `datus-cloudwatch-plugin` | `datus_cloudwatch_plugin` | [README](datus-cloudwatch-plugin/README.md) |
| `datus quicksight` | `datus-quicksight-plugin` | `datus_quicksight_plugin` | [README](datus-quicksight-plugin/README.md) |
| `datus mwaa` | `datus-mwaa-plugin` | `datus_mwaa_plugin` | [README](datus-mwaa-plugin/README.md) |

`datus-aws-common` (boto3 session/AssumeRole, config, error mapping, output
rendering, CLI helpers) is the shared **internal** library — it registers no
entry point and is not a plugin, but it *is* published as its own distribution so
the plugins can depend on it. See its [README](datus-aws-common/README.md).

## Layout

One directory per distribution, each following the standalone-plugin naming
triple:

```
datus-aws-plugins/
├── datus-aws-common/                 # shared library distribution (no entry point)
│   ├── pyproject.toml
│   ├── datus_aws_common/             # import package
│   └── tests/
└── datus-<service>-plugin/           # one distribution per service
    ├── pyproject.toml                # entry point + dependency on datus-aws-common
    ├── README.md
    ├── datus_<service>_plugin/       # import package
    │   ├── datus-plugin.yml          # the declarative plugin contract (manifest)
    │   ├── prompts/                  # system-prompt Jinja2 template
    │   ├── cli/                      # one module per command group
    │   └── skills/                   # bundled agent skills (SKILL.md per skill)
    └── tests/                        # contract + command tests
```

## Development

Part of the root [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/);
`datus-aws-common` is wired to each plugin via `[tool.uv.sources]` so the
workspace resolves it locally:

```bash
uv sync --all-extras
uv run --package datus-s3-plugin pytest datus-aws-plugins/datus-s3-plugin
uv run --package datus-aws-common pytest datus-aws-plugins/datus-aws-common
```

Run tests one distribution at a time: each has its own `tests/` dir reusing the
same file names (`conftest.py` / `test_commands.py` / `test_plugin_contract.py`),
so collecting them all at once from a shared root would collide.
