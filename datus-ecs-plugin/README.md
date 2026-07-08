# datus-ecs-plugin

A [Datus](https://datus.ai) plugin to run and monitor tasks on existing
**Amazon ECS / Fargate** clusters from `datus ecs ...` — scale services, run
one-off tasks, and read task logs. Backed by boto3. Cluster/service creation is
out of scope (use IaC).

```bash
pip install datus-ecs-plugin
```

## Configuration

Profiles live under `agent.plugins.ecs.<profile>` in Datus' `agent.yml`:

```yaml
agent:
  plugins:
    ecs:
      prod:
        default: true
        region: us-east-1
        cluster: prod-cluster       # optional default cluster
        log_group: /ecs/prod        # optional; for `tasks logs`
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Group | Subcommands |
|---|---|
| `clusters` | `list`, `describe` |
| `services` | `list`, `describe`, `events`, `scale` |
| `tasks` | `list`, `describe`, `run [--wait]`, `stop`, `logs` |
| `task-defs` | `list`, `describe` |

```bash
datus ecs services scale prod web 4
datus ecs tasks run --task-def etl:12 --launch-type FARGATE --subnet subnet-abc --security-group sg-abc --wait
datus ecs tasks logs prod arn:aws:ecs:...:task/abc --container app
```

Fargate is `tasks run --launch-type FARGATE`. `tasks run` starts billed compute
(always confirmed); `services scale` and `tasks stop` are routine.

## Exit codes

`0` success · `1` runtime/API error (also: task exited non-zero under `--wait`)
· `2` usage · `3` config error.

## Development

```bash
uv run --package datus-ecs-plugin pytest datus-ecs-plugin
```

Never imports `datus`; registers the `ecs` entry point in `datus.plugins`.
Shared boto3 plumbing lives in `datus-aws-common`. Bundled skills: `ecs` and
`ecs-setup`.
