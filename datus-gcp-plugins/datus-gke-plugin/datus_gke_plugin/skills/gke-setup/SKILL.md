---
name: gke-setup
description: Configure or troubleshoot a Google Kubernetes Engine profile and its paired datus k8s provider profile, including ADC, service-account impersonation, endpoint selection, scopes, retries, and verification.
requires_mutable_config: true
---

# GKE Setup

Add profiles under `agent.plugins.gke.<profile>` in the config file named by the
Plugins system-prompt section. Prefer Application Default Credentials (ADC).

## Collect and configure

Collect the profile name, `project`, `location` (region, zone, or `-`),
`cluster`, authentication mode, and desired Kubernetes namespace.

```yaml
agent:
  plugins:
    gke:
      prod:
        default: true
        project: data-prod
        location: asia-east1
        cluster: analytics

        # Authentication: omit for the ADC chain.
        # credentials_file: ${GOOGLE_APPLICATION_CREDENTIALS}
        # impersonate_service_account: datus@data-prod.iam.gserviceaccount.com
        # quota_project: billing-project
        # scopes: https://www.googleapis.com/auth/cloud-platform

        # Optional behavior.
        endpoint_mode: auto       # auto, public, private, or dns
        # api_endpoint: container.googleapis.com
        timeout: "60"
        max_attempts: "3"

    k8s:
      prod:
        provider: gke
        namespace: analytics
        allowed_namespaces: analytics
```

Use only `${ENV_VAR}` references for credential-file paths; require the
variable in the Datus process environment and never copy credential JSON into
configuration or chat. `credentials_file` loads that file instead of default
ADC. `impersonate_service_account` wraps the source identity, `quota_project`
sets quota/billing attribution, and `scopes` accepts comma-separated OAuth
scopes. `api_endpoint` overrides the Container API endpoint, not the cluster
Kubernetes endpoint.

Prefer workload identity, attached service accounts, or local ADC. The identity
needs Container API read access plus permission to obtain cluster credentials;
service-account impersonation also needs Token Creator on the target identity.

When the k8s profile has another name, set `provider_profile: prod`. If the
provider uses a non-default config file, set `provider_config` on the k8s
profile. Keep cloud credentials only in the GKE profile.

## Verify

```bash
datus gke --profile prod auth check -o json
datus gke --profile prod clusters describe -o json
datus k8s --profile prod version
datus k8s --profile prod auth can-i get pods -n analytics
```

If verification fails, distinguish ADC discovery/impersonation errors from
Container IAM denial and private/DNS endpoint reachability. Use
`endpoint_mode: public` only when public access is enabled; use `private` or
`dns` only from a network that can resolve and reach that endpoint.

If this environment cannot edit the active config, ask the deployment
administrator to make the change.
