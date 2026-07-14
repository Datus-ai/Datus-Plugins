---
name: ecs
description: Run and monitor tasks on existing Amazon ECS/Fargate clusters, scale services, inspect task definitions, and read task logs via the `datus ecs` CLI
---

# ECS

`datus ecs` operates **existing** Amazon ECS / Fargate clusters through boto3.
Cluster/service creation and task-definition registration are out of scope
(use IaC). Global usage:

```
datus ecs [--profile <env>] <group> <subcommand> [args...]
```

Add `-o json` for full output. `tasks run` starts billed compute.

## Clusters & services

```
datus ecs clusters list | describe <cluster>
datus ecs services list <cluster> | describe <cluster> <service> | events <cluster> <service>
datus ecs services scale <cluster> <service> <count>
```

## Tasks

```
datus ecs tasks list <cluster>
datus ecs tasks describe <cluster> <task>
datus ecs tasks run [<cluster>] --task-def <family[:rev]> [--launch-type FARGATE|EC2] \
    [--count N] [--subnet subnet-... --security-group sg-... --assign-public-ip] [--wait]
datus ecs tasks stop <cluster> <task> [--reason ...]
datus ecs tasks logs <cluster> <task> --container NAME [--stream-prefix ecs]
```

- **Fargate** = `tasks run --launch-type FARGATE`; awsvpc networking needs
  `--subnet` (and usually `--security-group`).
- `<cluster>` defaults to the profile's `cluster`.
- `run --wait` polls until the task is `STOPPED`; exit 0 if all containers exit
  0, else 1.
- `logs` reads CloudWatch stream `<stream-prefix>/<container>/<task-id>` from
  the configured `log_group` (the awslogs driver default).

## Task definitions

```
datus ecs task-defs list [--family FAMILY]
datus ecs task-defs describe <family[:revision] | arn>
```

## Exit codes

`0` success · `1` runtime/API error (also: task exited non-zero under `--wait`)
· `2` usage (also: no cluster / no log_group) · `3` config error.
