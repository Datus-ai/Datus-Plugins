---
name: emr-setup
description: Configure an environment profile for the `datus emr` plugin (AWS region + credentials, optional default cluster and S3 log_uri)
requires_mutable_config: true
---

# EMR Setup

Use this skill when `datus emr` is installed but has no configured environment.

## Config structure

Profiles live under `agent.plugins.emr.<profile>` in the config file named by
the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    emr:
      prod:
        default: true
        region: us-east-1
        cluster_id: j-XXXXXXXXXXXXX        # optional default cluster for `steps`
        log_uri: s3://my-emr-logs/emr/      # optional; base for `steps logs`
                                            # (usually the cluster's LogUri)

        # credentials — omit to use the standard AWS chain, otherwise any of:
        profile: my-aws-profile
        role_arn: arn:aws:iam::123456789012:role/datus-emr
        # access_key_id / secret_access_key — use ${VAR} refs
```

## Steps

1. Ask for `region`, optionally a default `cluster_id`, and the `log_uri` if
   the user wants `steps logs` (this is the cluster's configured S3 log
   location).
2. The principal needs `elasticmapreduce:ListClusters`, `DescribeCluster`,
   `ListInstances`, `ListSteps`, `DescribeStep`, `AddJobFlowSteps`,
   `CancelSteps`, plus `s3:GetObject` on the log bucket for `steps logs`.
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with `datus emr clusters list`.

## Troubleshooting

- `no cluster id` — pass the cluster id or set `cluster_id` in the profile.
- `no log_uri configured` — set `log_uri` to read step logs from S3.
- `steps logs` empty/NoSuchKey — logs appear after the step runs; the path is
  `<log_uri>/<cluster-id>/steps/<step-id>/stdout.gz`.
