---
name: gcs-setup
description: Configure or troubleshoot a Google Cloud Storage profile for datus gcs, including ADC, credential files, service-account impersonation, default bucket, scopes, custom endpoints, retries, permissions, and verification.
requires_mutable_config: true
---

# GCS Setup

Add profiles under `agent.plugins.gcs.<profile>` in the active Datus config.
Prefer Application Default Credentials (ADC).

## Collect and configure

Collect the profile name, GCP `project`, authentication mode, and optional
default `bucket`.

```yaml
agent:
  plugins:
    gcs:
      prod:
        default: true
        project: data-prod
        bucket: data-lake

        # Authentication: omit for ADC.
        # credentials_file: ${GOOGLE_APPLICATION_CREDENTIALS}
        # impersonate_service_account: datus@data-prod.iam.gserviceaccount.com
        # quota_project: billing-project
        # scopes: https://www.googleapis.com/auth/cloud-platform

        # api_endpoint: https://storage.googleapis.com
        timeout: "60"
        max_attempts: "3"
```

`bucket` enables bare-key arguments such as `reports/today.csv`; otherwise use
`gs://bucket/key`. Use only `${ENV_VAR}` references for credential-file paths,
require the variable in the Datus process environment, and never copy the JSON
credential into YAML or chat. `credentials_file` replaces default ADC;
`impersonate_service_account` wraps the source credential, `quota_project`
controls quota attribution, and `scopes` accepts comma-separated OAuth scopes.
Use `api_endpoint` only for a custom Storage endpoint or emulator.

Prefer workload identity, an attached service account, or local ADC. Grant
object read/list for browsing; add create/update for `cp`/`sync`; add delete for
`mv`/`rm`; add bucket update for lifecycle replacement. Service-account
impersonation requires Token Creator on the target. Signed URLs require a
signing-capable identity in addition to access for the intended operation.

## Verify

```bash
datus gcs --profile prod buckets list -o json
datus gcs --profile prod ls gs://data-lake/ --limit 5 -o json
```

If bucket listing is intentionally forbidden, verify a known allowed prefix
directly. For failures, distinguish missing ADC/environment variables, service
account impersonation denial, object-vs-bucket IAM denial, and custom endpoint
connectivity. A configured default bucket does not grant access by itself.

If this environment cannot edit the active config, ask the deployment
administrator to make the change.
