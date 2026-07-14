---
name: glue-setup
description: Configure an environment profile for the `datus glue` plugin (AWS region + credentials, optional cross-account catalog_id)
---

# Glue Setup

Use this skill when `datus glue` is installed but has no configured environment,
or to add another environment.

## Config structure

Profiles live under `agent.plugins.glue.<profile>` in the config file named by
the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    glue:
      prod:
        default: true
        region: us-east-1
        catalog_id: "123456789012"   # optional — a cross-account Data Catalog id

        # credentials — omit to use the standard AWS chain, otherwise any of:
        profile: my-aws-profile
        access_key_id: ${AWS_ACCESS_KEY_ID}         # secret — env var reference
        secret_access_key: ${AWS_SECRET_ACCESS_KEY} # secret
        role_arn: arn:aws:iam::123456789012:role/datus-glue   # assume this role
```

## Steps

1. Ask for `region` and the auth method (prefer the AWS chain; `${VAR}` for any
   keys). Ask whether the catalog is in another account (`catalog_id`).
2. The IAM principal needs read access: `glue:GetDatabases`, `glue:GetTables`,
   `glue:GetTable`, `glue:SearchTables`, `glue:GetPartitions`,
   `glue:GetCrawler(s)`, `glue:GetJob(s)`, `glue:GetJobRun(s)`,
   `glue:GetConnection(s)`, plus `logs:GetLogEvents` for `jobs logs`. To run
   work: `glue:StartCrawler`, `glue:StartJobRun`, `glue:BatchStopJobRun`. For
   catalog writes: the matching `Create*/Update*/Delete*` actions.
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with a cheap read-only call: `datus glue catalog databases --limit 5`.

## Troubleshooting

- `no AWS credentials found` / `no AWS region configured` — set credentials or
  `region`.
- `EntityNotFoundException` — the database/table/crawler/job name is wrong, or
  it lives in another account's catalog (set `catalog_id`).
- `jobs logs` empty — the run may not have produced logs yet, or the log group
  differs; the plugin reads `/aws-glue/jobs/output` (`--error` for the error
  log group), stream = the job run id.
