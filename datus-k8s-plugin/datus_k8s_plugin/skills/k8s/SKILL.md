---
name: k8s
description: Inspect and operate namespace-scoped Kubernetes data workloads through `datus k8s`
---

# Kubernetes

Use this skill for Kubernetes workload status, pod logs, events, metrics,
authorization checks, manifests, scaling, and rollout restart/status.

## Safety and routing

- Select the Datus environment before the Kubernetes command:
  `datus k8s --profile <profile> get pods -n <namespace>`.
- Only namespaces listed by the selected profile are accessible.
- `-A`, cluster-scoped resources, kubeconfig overrides, impersonation, exec,
  attach, cp, proxy, and port-forward are intentionally unavailable.
- Read-only commands are safe to run. Datus asks before every mutating command.
- The output formats are `table`, `wide`, `json`, `yaml`, and `name`.

## Diagnose a workload

```bash
datus k8s --profile prod get jobs -n analytics -o wide
datus k8s --profile prod describe job daily-etl -n analytics
datus k8s --profile prod get pods -n analytics -l job-name=daily-etl
datus k8s --profile prod logs daily-etl-abcde -n analytics --tail 200
datus k8s --profile prod events -n analytics --for job/daily-etl
datus k8s --profile prod top pod -n analytics --sort-by memory
datus k8s --profile prod auth can-i create jobs -n analytics
```

Use `-o json` or `-o yaml` when subsequent reasoning needs full object fields.

## Change a workload

```bash
datus k8s --profile prod apply -f ./k8s/job.yaml -n analytics
datus k8s --profile prod wait job/daily-etl --for=condition=Complete --timeout=30m -n analytics
datus k8s --profile prod scale deployment/worker --replicas=8 -n analytics
datus k8s --profile prod rollout restart deployment/worker -n analytics
datus k8s --profile prod rollout status deployment/worker --timeout=10m -n analytics
```

`apply` is always Server-Side Apply. Files must be local or stdin; directories,
URLs, Kustomize, JSONPath, and Go templates are not supported.
