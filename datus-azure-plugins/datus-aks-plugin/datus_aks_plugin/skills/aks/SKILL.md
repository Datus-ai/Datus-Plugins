---
name: aks
description: Inspect Azure Kubernetes Service clusters, node pools, maintenance configurations, upgrades, and authentication, and hand Microsoft Entra credentials to datus k8s. Use for AKS inventory, health, upgrade planning, or Kubernetes access through an AKS provider profile.
---

# AKS

Use `datus aks` for AKS control-plane inventory and authentication diagnostics.
Use the matching `datus k8s` profile for Kubernetes objects and workloads.

```bash
datus aks [--profile <env>] <command> [args...]
```

Public inspection commands accept `-o table|json|yaml|plain`. Prefer JSON for
complete Azure SDK fields.

## Command catalogue

```bash
datus aks clusters list [-o json]
datus aks clusters describe [-o json]
datus aks nodepools list [-o json]
datus aks nodepools describe <name> [-o json]
datus aks maintenance list [-o json]
datus aks maintenance describe <name> [-o json]
datus aks upgrades list [-o json]
datus aks auth check [-o json]
datus aks kubernetes cluster
datus aks kubernetes credential
```

- `clusters list` inventories the subscription; `clusters describe` reads the
  configured resource-group cluster.
- `nodepools` inspects agent-pool size, VM type, version, and provisioning state.
- `maintenance` reads configured maintenance windows.
- `upgrades list` returns the configured cluster's upgrade profile.
- `auth check` acquires a Microsoft Entra token but prints only status/expiry.
- `kubernetes cluster` emits the machine-readable endpoint/CA contract.
  `kubernetes credential` emits an Entra bearer token and is denied to Agent
  bash. Never invoke or print it; the k8s plugin calls it internally.

These public commands are read-only, but Azure RBAC must permit the relevant
Managed Clusters reads and user-credential action. Never put a client secret or
token on the command line.

## Typical workflows

Plan an upgrade:

```bash
datus aks --profile prod clusters describe -o json
datus aks --profile prod nodepools list -o json
datus aks --profile prod upgrades list -o json
datus aks --profile prod maintenance list -o json
```

Inspect Kubernetes through the provider handoff:

```bash
datus aks --profile prod auth check -o json
datus k8s --profile prod version
datus k8s --profile prod get pods -n analytics
```

The k8s profile must use `provider: aks`; set `provider_profile` when names
differ. AKS uses an Entra access token for the configured Kubernetes server app
ID. Private clusters still require DNS and network access from the Datus host.

## Exit codes

`0` success · `1` runtime/API error · `2` usage error · `3` config error ·
`8` missing dependency · `130` interrupted.
