---
name: quicksight-setup
description: Configure an environment profile for the `datus quicksight` plugin (AWS region + credentials and the required aws_account_id)
---

# QuickSight Setup

Use this skill when `datus quicksight` is installed but has no configured
environment.

## Config structure

Profiles live under `agent.plugins.quicksight.<profile>` in the config file
named by the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    quicksight:
      prod:
        default: true
        region: us-east-1
        aws_account_id: "123456789012"   # REQUIRED — every QuickSight API needs it
        namespace: default               # optional (default 'default')
        identity_region: us-east-1       # optional — region of the QuickSight identity
                                         # store for users/groups/namespaces (defaults to region)

        # credentials — omit to use the standard AWS chain, otherwise any of:
        profile: my-aws-profile
        role_arn: arn:aws:iam::123456789012:role/datus-quicksight
        # access_key_id / secret_access_key — use ${VAR} refs
```

## Steps

1. Ask for `region` and the **`aws_account_id`** (the account QuickSight runs
   in — required by every API call). Auth via the AWS chain is preferred.
2. The principal needs the QuickSight actions for what they'll use: read
   (`quicksight:List*`, `Describe*`), SPICE (`CreateIngestion`,
   `CancelIngestion`, `*RefreshSchedule*`), embed
   (`GenerateEmbedUrlForRegisteredUser` / `...AnonymousUser`), asset lifecycle
   (`Create*`/`Update*`/`Delete*` on datasets/dashboards/analyses/etc.),
   identity (`RegisterUser`, `CreateGroup`, `*GroupMembership`, `*Namespace`),
   and asset bundles (`StartAssetBundle*Job`, `DescribeAssetBundle*Job`).
3. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
4. Verify with `datus quicksight datasets list`.

## Troubleshooting

- `aws_account_id is required` — set it in the profile.
- `AccessDenied` — the principal lacks the QuickSight actions above, or the
  QuickSight subscription is in another region.
- `embed-url` fails — the `--user-arn` must be an existing registered
  QuickSight user in this account/namespace.
