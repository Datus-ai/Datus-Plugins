---
name: adls-setup
description: Configure or troubleshoot an Azure Blob or ADLS Gen2 profile for datus adls, including Entra DefaultAzureCredential, service principals, managed identity, account key or SAS, sovereign clouds, HNS behavior, permissions, and verification.
requires_mutable_config: true
---

# ADLS Setup

Add profiles under `agent.plugins.adls.<profile>` in the active Datus config.
Prefer an Entra identity through `DefaultAzureCredential`.

## Collect and configure

Collect the profile name, DFS `account_url`, optional default
filesystem/container, Azure cloud, authentication mode, and whether the account
has hierarchical namespace (HNS) enabled.

```yaml
agent:
  plugins:
    adls:
      prod:
        default: true
        account_url: https://data.dfs.core.windows.net
        container: lake
        cloud: public              # public, china, or government

        # Entra service principal; omit for DefaultAzureCredential.
        # tenant_id: 00000000-0000-0000-0000-000000000000
        # client_id: 00000000-0000-0000-0000-000000000000
        # client_secret: ${AZURE_CLIENT_SECRET}
        # managed_identity_client_id: 00000000-0000-0000-0000-000000000000

        # Alternative shared credentials; configure at most one.
        # account_key: ${AZURE_STORAGE_ACCOUNT_KEY}
        # sas_token: ${AZURE_STORAGE_SAS_TOKEN}

        timeout: "60"
        max_attempts: "3"
```

`account_url` must be an HTTPS DFS endpoint; the plugin derives its Blob
endpoint for SAS generation. `container` permits bare paths. `cloud` selects
the correct Entra authority. For a service principal, configure `tenant_id`,
`client_id`, and `client_secret` together. A user-assigned identity uses
`managed_identity_client_id` through `DefaultAzureCredential`.

Use only `${ENV_VAR}` references for `client_secret`, `account_key`, or
`sas_token`, and require them in the Datus process environment. Never store
literal secrets. Configure at most one of account key and SAS. Prefer Entra;
account keys are broad credentials and an existing SAS is limited by its own
scope, permissions, and expiry.

Grant the data-plane role `Storage Blob Data Reader` for reads and
`Storage Blob Data Contributor` for writes/deletes. Management roles such as
`Contributor`, `Reader`, or `Storage Account Contributor` administer the account
and do not grant access to its data. `sas` additionally needs
`Microsoft.Storage/storageAccounts/blobServices/generateUserDelegationKey/action`.

Separately from Azure RBAC, HNS accounts also enforce POSIX ACLs: directory
execute/traverse on every parent and read/write on the file itself may be
required even when a data-plane role is already assigned. ACL commands are
meaningful only with HNS enabled, and `acl set` requires being the owning user
or holding `Storage Blob Data Owner`.

## Verify

```bash
datus adls --profile prod filesystems list -o json
datus adls --profile prod ls abfss://lake/ --limit 5 -o json
```

If filesystem enumeration is intentionally denied, verify a known allowed path
directly. For failures, separate credential-chain/tenant issues, Azure RBAC,
HNS path ACLs, SAS scope/expiry, and an incorrect sovereign-cloud endpoint.
Remember that `account_url` is the DFS endpoint, not the Blob endpoint.

If this environment cannot edit the active config, ask the deployment
administrator to make the change.
