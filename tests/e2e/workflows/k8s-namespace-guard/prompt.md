Create a ConfigMap named `e2e-allowed` in `{{NAMESPACE}}`, then prove with the
plugin that it exists. Also verify that the configured profile refuses an
attempt to read ConfigMaps from `kube-system`. Write both observed outcomes to
`results/namespace-guard.json`. Use only `datus k8s --profile e2e`.
