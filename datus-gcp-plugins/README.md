# Datus GCP plugins

| Command | Distribution | Purpose |
|---|---|---|
| `datus gke` | `datus-gke-plugin` | Inspect GKE and authenticate `datus k8s` |
| `datus gcs` | `datus-gcs-plugin` | Browse and move GCS object data |

Both depend on `datus-gcp-common`, an internal library for ADC, service-account
files, impersonation, error mapping and output rendering.
