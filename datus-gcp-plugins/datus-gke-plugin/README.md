# datus-gke-plugin

Read-only GKE inspection and short-lived Kubernetes authentication without the
`gcloud` CLI. Pair a GKE profile with a same-named `k8s` profile containing
`provider: gke`; cloud credentials remain in the GKE profile and tokens are
never persisted.
