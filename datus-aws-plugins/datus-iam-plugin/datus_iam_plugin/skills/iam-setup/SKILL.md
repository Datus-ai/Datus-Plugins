---
name: iam-setup
description: Configure an environment profile for the `datus iam` plugin (AWS credentials for read-only IAM inspection)
requires_mutable_config: true
---

# IAM Setup

Use this skill when `datus iam` is installed but has no configured environment.

## Config structure

Profiles live under `agent.plugins.iam.<profile>` in the config file named by
the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    iam:
      prod:
        default: true
        region: us-east-1            # IAM is global; region is used for the endpoint

        # credentials — omit to use the standard boto3 chain, otherwise any of:
        profile: my-aws-profile
        access_key_id: ${AWS_ACCESS_KEY_ID}         # secret — env var reference
        secret_access_key: ${AWS_SECRET_ACCESS_KEY} # secret
        role_arn: arn:aws:iam::123456789012:role/datus-readonly   # assume this role
```

## Steps

1. Ask for the auth method (prefer the AWS chain; `${VAR}` for any keys).
2. The IAM principal needs read/simulate access: `iam:Get*`, `iam:List*`,
   `iam:SimulatePrincipalPolicy`, `iam:SimulateCustomPolicy`, and
   `sts:GetCallerIdentity`. This plugin performs **no** write actions.
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with `datus iam whoami`.

## Troubleshooting

- `no AWS credentials found` — set credentials or run where the chain resolves.
- `AccessDenied` listing roles/policies — the principal lacks `iam:List*` /
  `iam:Get*`; simulation additionally needs `iam:Simulate*`.
