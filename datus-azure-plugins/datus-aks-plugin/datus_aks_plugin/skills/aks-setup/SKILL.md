---
name: aks-setup
description: Configure or troubleshoot an Azure Kubernetes Service profile and its paired datus k8s provider profile, including Azure cloud, DefaultAzureCredential, service principals, managed identity, private endpoints, and verification.
requires_mutable_config: true
---

# AKS Setup

Add profiles under `agent.plugins.aks.<profile>` in the active Datus config.
Prefer `DefaultAzureCredential` with workload or managed identity.

## Collect and configure

Collect the profile name, `subscription_id`, `resource_group`, `cluster`, Azure
cloud, authentication mode, and Kubernetes namespace.

```yaml
agent:
  plugins:
    aks:
      prod:
        default: true
        subscription_id: 00000000-0000-0000-0000-000000000000
        resource_group: data-prod
        cluster: analytics
        cloud: public              # public, china, or government

        # Omit these to use DefaultAzureCredential.
        # tenant_id: 00000000-0000-0000-0000-000000000000
        # client_id: 00000000-0000-0000-0000-000000000000
        # client_secret: ${AZURE_CLIENT_SECRET}
        # managed_identity_client_id: 00000000-0000-0000-0000-000000000000

        # kubernetes_server_id: 6dae42f8-4368-4678-94ff-3960e28e3630
        use_private_endpoint: "false"
        timeout: "60"
        max_attempts: "3"

    k8s:
      prod:
        provider: aks
        namespace: analytics
        allowed_namespaces: analytics
```

For a service principal, configure `tenant_id`, `client_id`, and
`client_secret` together. Store only `${ENV_VAR}` for the secret and require it
in the Datus process environment. For a user-assigned managed identity, use
`managed_identity_client_id`; do not also invent a client secret. Interactive
browser authentication is disabled.

`cloud` selects authority and Resource Manager endpoints. Override
`kubernetes_server_id` only for an AKS environment that uses another API server
application ID. `use_private_endpoint` selects the private FQDN returned by AKS;
the Datus host must resolve and reach it.

Grant Azure RBAC read access to the cluster resources and permission for
`listClusterUserCredential/action`; Kubernetes RBAC separately controls what
the resulting identity can do inside the cluster.

If k8s and AKS profile names differ, add `provider_profile: prod` to k8s. For a
non-default provider config file, also set `provider_config`. Keep Azure
credentials only in the AKS profile.

## Verify

```bash
datus aks --profile prod auth check -o json
datus aks --profile prod clusters describe -o json
datus k8s --profile prod version
datus k8s --profile prod auth can-i get pods -n analytics
```

For failures, separate credential-chain/tenant errors, Azure RBAC denial,
Kubernetes RBAC denial, and private DNS/network reachability. Ensure `cloud`
matches the subscription's sovereign cloud.

If this environment cannot edit the active config, ask the deployment
administrator to make the change.
