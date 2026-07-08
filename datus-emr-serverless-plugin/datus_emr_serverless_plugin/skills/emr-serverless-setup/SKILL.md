---
name: emr-serverless-setup
description: Configure an environment profile for the `datus emr-serverless` plugin (AWS region + credentials, default application id and execution role)
---

# EMR Serverless Setup

Use this skill when `datus emr-serverless` is installed but has no configured
environment.

## Config structure

Profiles live under `agent.plugins.emr-serverless.<profile>` in the config file
named by the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    emr-serverless:
      prod:
        default: true
        region: us-east-1
        application_id: 00abc123          # optional default application
        execution_role_arn: arn:aws:iam::123456789012:role/emr-serverless-exec  # optional default

        # credentials — omit to use the standard AWS chain, otherwise any of:
        profile: my-aws-profile
        role_arn: arn:aws:iam::123456789012:role/datus-emr
        # access_key_id / secret_access_key — use ${VAR} refs
```

## Steps

1. Ask for `region`, and optionally a default `application_id` and
   `execution_role_arn` (the role the Spark job assumes to read/write data).
2. The calling principal needs `emr-serverless:GetApplication`,
   `ListApplications`, `StartApplication`, `StopApplication`, `StartJobRun`,
   `GetJobRun`, `ListJobRuns`, `CancelJobRun`, `GetDashboardForJobRun`, and
   `iam:PassRole` for the execution role.
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with `datus emr-serverless applications list`.

## Troubleshooting

- `no application id` / `no execution role` — pass them as args, or set
  `application_id` / `execution_role_arn` in the profile.
- Job stuck in `SCHEDULED` — the application may be stopped; run
  `applications start <app-id>`.
