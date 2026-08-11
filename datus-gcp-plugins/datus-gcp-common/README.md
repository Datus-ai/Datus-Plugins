# datus-gcp-common

Internal library shared by the Datus GKE and GCS plugins. It owns ADC,
service-account-file and service-account-impersonation authentication plus
domain-neutral CLI/output/error helpers. It is not a Datus plugin and declares
no `datus.plugins` entry point.
