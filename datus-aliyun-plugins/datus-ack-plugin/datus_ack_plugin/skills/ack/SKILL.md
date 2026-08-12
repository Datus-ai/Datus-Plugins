---
name: ack
description: Inspect Alibaba Cloud ACK clusters, node pools, add-ons, tasks, and authentication, and hand temporary ACK credentials to datus k8s. Use for ACK inventory, health, change diagnostics, add-on status, or Kubernetes access through an ACK provider profile.
---

# ACK

Use `datus ack` for ACK control-plane inventory and authentication diagnostics.
Use the matching `datus k8s` profile for Kubernetes objects and workloads.

```bash
datus ack [--profile <env>] <command> [args...]
```

Public inspection commands accept `-o table|json|yaml|plain`. Prefer JSON for
complete OpenAPI responses.

## Command catalogue

```bash
datus ack clusters list [-o json]
datus ack clusters describe [-o json]
datus ack nodepools list [-o json]
datus ack nodepools describe <name> [-o json]
datus ack addons list [-o json]
datus ack addons describe [-o json]
datus ack tasks list [-o json]
datus ack tasks describe <name> [-o json]
datus ack auth check [-o json]
datus ack kubernetes access
datus ack kubernetes cluster
datus ack kubernetes credential
```

- `clusters list` inventories clusters in the configured region;
  `clusters describe` reads the configured cluster ID.
- `nodepools` inspects the configured cluster's node pools; `name` is a node
  pool ID.
- `addons list` reads available/current add-on versions; `addons describe`
  reads cluster add-on upgrade status and takes no name argument.
- `tasks` inspects cluster tasks; `tasks describe` takes a task ID.
- `auth check` requests temporary user kubeconfig material but prints only
  authentication status, credential type, and expiry.
- `kubernetes access` emits endpoint/CA plus the initial credential in one
  provider call. `kubernetes credential` emits a refreshed credential. Both
  contain secret material and are denied to Agent bash; never invoke or print
  them. The k8s plugin calls them internally. `kubernetes cluster` is the
  backward-compatible, non-secret endpoint/CA command.

All public inspection commands are read-only, but RAM permissions must allow
the matching CS OpenAPI reads and temporary user kubeconfig retrieval. Never
put access keys, STS tokens, kubeconfig, or bearer tokens on the command line.

## Typical workflows

Diagnose a cluster change:

```bash
datus ack --profile prod clusters describe -o json
datus ack --profile prod tasks list -o json
datus ack --profile prod tasks describe <task-id> -o json
datus ack --profile prod addons describe -o json
```

Inspect Kubernetes through the provider handoff:

```bash
datus ack --profile prod auth check -o json
datus k8s --profile prod version
datus k8s --profile prod get pods -n analytics
```

The k8s profile must use `provider: ack`; set `provider_profile` when names
differ. ACK temporary kubeconfigs may contain either a bearer token or a client
certificate/private key pair; the provider maps both forms to the Kubernetes
`ExecCredential` v1 contract. When `use_private_endpoint` is enabled, the Datus
host must reach the private API server.

## Exit codes

`0` success · `1` runtime/API error · `2` usage error · `3` config error ·
`8` missing dependency.
