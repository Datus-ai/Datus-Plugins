# datus-k8s-plugin

A Datus plugin for inspecting and operating namespace-scoped Kubernetes data
workloads through `datus k8s`. Its command surface follows familiar kubectl
verbs while using the Kubernetes Python client rather than a kubectl binary.

## Install

```bash
datus plugin install src:./datus-k8s-plugin
```

## Configure

```yaml
agent:
  plugins:
    k8s:
      prod:
        default: true
        kubeconfig: ./conf/kubeconfig.yaml
        # context is optional; current-context is used when omitted
        context: prod
        namespace: analytics
        allowed_namespaces: analytics,analytics-staging
```

Relative kubeconfig paths resolve from the directory where Datus is started and
cannot escape it. Absolute paths and `${KUBECONFIG}` are also accepted.
Credentials remain inside kubeconfig.

## Commands

Read-only:

```bash
datus k8s get pods -n analytics
datus k8s logs pod-name -n analytics --tail 200
datus k8s events -n analytics
datus k8s top pod -n analytics
datus k8s rollout status deployment/worker -n analytics
```

State-changing commands (`create`, `apply`, `delete`, `patch`, `scale`,
`rollout restart`, `label`, and `annotate`) always require agent confirmation.

This is a deliberate kubectl-style subset. It does not support cluster-scoped
resources, `-A`, exec/attach/cp, port-forward/proxy, Kustomize, kubeconfig
mutation, JSONPath, Go templates, or kubectl plugins.

## Develop

```bash
uv run --package datus-k8s-plugin pytest datus-k8s-plugin
```

The package never imports `datus`; its complete plugin contract is declared in
`datus_k8s_plugin/datus-plugin.yml`.
