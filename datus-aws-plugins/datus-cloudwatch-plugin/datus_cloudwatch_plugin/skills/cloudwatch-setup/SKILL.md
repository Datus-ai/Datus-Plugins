---
name: cloudwatch-setup
description: Configure an environment profile for the `datus cloudwatch` plugin (AWS region + credentials)
requires_mutable_config: true
---

# CloudWatch Setup

Use this skill when `datus cloudwatch` is installed but has no configured
environment, or when the user wants to add another environment (e.g. a second
account or region).

## Config structure

Profiles live under `agent.plugins.cloudwatch.<profile>` in the config file
named by the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    cloudwatch:
      prod:
        default: true                # mark exactly one profile as default
        region: us-east-1            # required in practice (or resolvable from the AWS chain)

        # credentials — omit entirely to use the standard boto3 chain
        # (env vars / ~/.aws / instance profile / IRSA). Otherwise any of:
        profile: my-aws-profile      # a named profile in ~/.aws/config
        access_key_id: ${AWS_ACCESS_KEY_ID}         # secret — env var reference
        secret_access_key: ${AWS_SECRET_ACCESS_KEY} # secret
        role_arn: arn:aws:iam::123456789012:role/datus-readonly  # assume this role
        role_session_name: datus                    # optional
        external_id: team-a                          # optional

        # optional tuning
        timeout: 60                  # botocore connect/read timeout seconds
        max_attempts: 3              # botocore retry attempts
```

## Steps

1. Ask the user for the AWS `region` and how they authenticate. Prefer the
   standard AWS chain (no keys in config); if they must supply keys, have them
   `export` env vars and reference `${VAR}` — never write a literal secret.
2. The IAM principal needs read access: `logs:Describe*`, `logs:GetLogEvents`,
   `logs:FilterLogEvents`, `logs:StartQuery`, `logs:GetQueryResults`,
   `cloudwatch:List*`, `cloudwatch:Describe*`, `cloudwatch:GetMetric*`,
   `cloudwatch:GetDashboard` (+ `cloudwatch:SetAlarmState` only if they use
   `alarms set-state`).
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with a cheap read-only call: `datus cloudwatch logs groups --limit 5`.

## Troubleshooting

- `no AWS credentials found` — the chain resolved nothing; set a `profile` or
  keys, or run where an instance role/IRSA is available.
- `no AWS region configured` — set `region` in the profile.
- `AccessDeniedException` — the principal is missing one of the read actions
  above; use `datus iam simulate` (if installed) to pinpoint the missing action.
