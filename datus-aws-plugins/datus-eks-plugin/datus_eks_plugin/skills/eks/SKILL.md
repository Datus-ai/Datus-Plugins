---
name: eks
description: Inspect Amazon EKS clusters, node groups, add-ons, access entries, Fargate profiles, updates, upgrade insights, and caller identity
---

# Amazon EKS

Use `datus eks` for EKS control-plane inventory and diagnostics. Every public
operational command is read-only.

Start with:

```bash
datus eks clusters describe -o json
datus eks auth whoami -o json
```

Then inspect the relevant resource with `nodegroups`, `addons`,
`access-entries`, `fargate-profiles`, `updates`, or `insights`. Prefer JSON when
you need complete AWS response fields; table output is a compact summary.

The configured EKS profile owns the cluster name and AWS authentication. Do not
pass credentials on a command line, do not invoke the AWS CLI, and do not run
`datus eks kubernetes credential` to print a bearer token. For Kubernetes
resources inside namespaces, use the matching `datus k8s --profile ...`
profile instead.
