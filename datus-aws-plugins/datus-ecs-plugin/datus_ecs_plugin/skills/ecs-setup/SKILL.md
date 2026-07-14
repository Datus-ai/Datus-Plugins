---
name: ecs-setup
description: Configure an environment profile for the `datus ecs` plugin (AWS region + credentials, optional default cluster and CloudWatch log group)
---

# ECS Setup

Use this skill when `datus ecs` is installed but has no configured environment.

## Config structure

Profiles live under `agent.plugins.ecs.<profile>` in the config file named by
the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    ecs:
      prod:
        default: true
        region: us-east-1
        cluster: prod-cluster           # optional default cluster
        log_group: /ecs/prod            # optional; awslogs group for `tasks logs`

        # credentials — omit to use the standard AWS chain, otherwise any of:
        profile: my-aws-profile
        role_arn: arn:aws:iam::123456789012:role/datus-ecs
        # access_key_id / secret_access_key — use ${VAR} refs
```

## Steps

1. Ask for `region`, optionally a default `cluster`, and the `log_group` if the
   user wants `tasks logs` (the awslogs group the task definition writes to).
2. The principal needs `ecs:List*`, `ecs:Describe*`, `ecs:UpdateService`,
   `ecs:RunTask`, `ecs:StopTask`, `iam:PassRole` (for the task/execution roles),
   and `logs:GetLogEvents` for `tasks logs`.
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with `datus ecs clusters list`.

## Troubleshooting

- `no cluster` — pass the cluster or set `cluster` in the profile.
- `no log_group configured` — set `log_group` to read task logs.
- Fargate `run` fails with a networking error — pass `--subnet` (awsvpc mode
  requires subnets and usually security groups).
