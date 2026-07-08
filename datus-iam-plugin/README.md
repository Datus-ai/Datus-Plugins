# datus-iam-plugin

A [Datus](https://datus.ai) plugin for **read-only** AWS IAM inspection from
`datus iam ...` — roles, users, managed policies, policy documents, and
permission simulation (the fastest way to diagnose a data job's `AccessDenied`).
This plugin has **no** mutating commands by design.

```bash
pip install datus-iam-plugin
```

## Configuration

Profiles live under `agent.plugins.iam.<profile>` in Datus' `agent.yml`:

```yaml
agent:
  plugins:
    iam:
      prod:
        default: true
        region: us-east-1
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Group | Subcommands |
|---|---|
| `whoami` | STS caller identity |
| `roles` | `list`, `get`, `attached`, `trust` |
| `users` | `list`, `get`, `attached` |
| `policies` | `list`, `get`, `document` |
| `simulate` | `principal`, `custom` |

```bash
datus iam whoami
datus iam simulate principal arn:aws:iam::123:role/glue-job --action s3:GetObject --resource 'arn:aws:s3:::lake/*'
datus iam roles attached glue-job
```

## Exit codes

`0` success · `1` runtime/API error · `2` usage · `3` config error.

## Development

```bash
uv run --package datus-iam-plugin pytest datus-iam-plugin
```

Never imports `datus`; implements the plugin contract and registers the `iam`
entry point in `datus.plugins`. Shared boto3 plumbing lives in
`datus-aws-common`. Bundled skills: `iam` and `iam-setup`.

## Agent bash permissions

Every command is read-only, so all run everywhere (allowed under both `normal`
and `auto`). There is nothing to confirm. User rules in `agent.yml` always win.
