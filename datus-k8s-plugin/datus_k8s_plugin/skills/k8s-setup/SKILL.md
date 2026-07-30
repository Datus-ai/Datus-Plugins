---
name: k8s-setup
description: Configure a kubeconfig-backed environment for the `datus k8s` plugin
requires_mutable_config: true
---

# Kubernetes Setup

Use this skill when the plugin is unconfigured or the user wants another
Kubernetes environment.

If this deployment cannot edit the active Datus config, tell the user to ask
the deployment administrator instead.

## Information to collect

1. A kubeconfig path. It may be absolute, `${KUBECONFIG}`, or relative to the
   current Datus project directory, such as `./conf/kubeconfig.yaml`.
2. An optional context. If omitted, the plugin uses kubeconfig
   `current-context` at runtime.
3. The default namespace.
4. Every allowed namespace, as a comma-separated string.

Do not copy tokens, certificates, or client keys out of kubeconfig.

## Configuration

Add a profile under `agent.plugins.k8s` in the config file named by the
`## Plugins` system-prompt section:

```yaml
agent:
  plugins:
    k8s:
      prod:
        default: true
        kubeconfig: ./conf/kubeconfig.yaml
        # context: prod-cluster       # optional; otherwise current-context
        namespace: analytics
        allowed_namespaces: analytics,analytics-staging
        request_timeout: 30s
        field_manager: datus-k8s
```

Relative paths are resolved from the directory where Datus is started and may
not escape it through `..` or symlinks.

## Verify

```bash
datus k8s --profile prod version
datus k8s --profile prod auth can-i get pods -n analytics
datus k8s --profile prod get pods -n analytics
```
