---
name: gke
description: Inspect Google Kubernetes Engine clusters, node pools, operations, server versions, and authentication, and hand GKE credentials to the datus k8s plugin. Use for GKE inventory, health, upgrade diagnostics, or Kubernetes access through a GKE provider profile.
---

# GKE

Use `datus gke` for GKE control-plane inventory and authentication diagnostics.
Use the matching `datus k8s` profile for Kubernetes objects and workloads.

```bash
datus gke [--profile <env>] <command> [args...]
```

Add `-o table|json|yaml|plain` to public inspection commands. Prefer JSON when
downstream work needs complete provider response fields.

## Command catalogue

```bash
datus gke clusters list [-o json]
datus gke clusters describe [-o json]
datus gke nodepools list [-o json]
datus gke nodepools describe <name> [-o json]
datus gke operations list [-o json]
datus gke operations describe <name> [-o json]
datus gke server-config describe [-o json]
datus gke auth check [-o json]
datus gke kubernetes cluster
datus gke kubernetes credential
```

- `clusters list` lists clusters in the configured project and location;
  `clusters describe` reads the configured cluster.
- `nodepools` inspects node-pool status, version, and capacity.
- `operations` diagnoses recent GKE changes; `describe` accepts an operation ID
  or full resource name.
- `server-config describe` returns valid/default Kubernetes versions for the
  configured location and is useful before an upgrade.
- `auth check` refreshes ADC and prints only authentication status and expiry.
- `kubernetes cluster` emits the machine-readable cluster endpoint/CA contract.
  `kubernetes credential` emits a bearer token and is denied to Agent bash.
  Never invoke or print it; the k8s plugin calls it internally.

All public inventory commands are read-only. They still require Google IAM
permissions for the corresponding Container API reads. Never put a credential
file or access token on the command line.

## Typical workflows

Diagnose a stalled change:

```bash
datus gke --profile prod clusters describe -o json
datus gke --profile prod operations list -o json
datus gke --profile prod operations describe <operation-id> -o json
```

Check versions before planning an upgrade:

```bash
datus gke --profile prod nodepools list -o json
datus gke --profile prod server-config describe -o json
```

Operate workloads through the same-named k8s profile:

```bash
datus gke --profile prod auth check -o json
datus k8s --profile prod version
datus k8s --profile prod get pods -n analytics
```

The k8s profile must use `provider: gke`. Set `provider_profile` only when its
name differs from the GKE profile. Endpoint selection follows `endpoint_mode`:
`auto`, `public`, `private`, or `dns`; network reachability to the selected
endpoint is still required.

## Exit codes

`0` success · `1` runtime/API error · `2` usage error · `3` config error ·
`8` missing dependency · `130` interrupted.
