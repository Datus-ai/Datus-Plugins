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
- `-A`, cluster-scoped resources, kubeconfig overrides, impersonation, attach,
  cp, proxy, and port-forward are intentionally unavailable.
- `exec` runs one non-interactive command with stdin and TTY disabled. Datus asks
  before every exec, in every permission profile.
- Read-only commands are safe to run. Datus asks before every mutating command.
- The output formats are `table`, `wide`, `json`, `yaml`, `name`, and
  `jsonpath={.path.to.field}`.

## Read one field instead of a whole object

`-o jsonpath=` prints a single field per object, so reading a status never
requires fetching the full document and picking through it:

```bash
datus k8s --profile prod get flinkdeployment orders -n analytics \
  -o 'jsonpath={.status.jobStatus.state}'
datus k8s --profile prod get flinkdeployment orders -n analytics -o 'jsonpath={.status.error}'
datus k8s --profile prod get pod worker-abcde -n analytics \
  -o 'jsonpath={.status.containerStatuses[0].state.waiting.reason}'
```

Only `.field` and `[index]` steps are supported; a missing field prints an empty
line. Never wrap these commands in a shell or Python script that re-parses `-o
json` — ask for the field directly.

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

`get pods` reports `READY` and `RESTARTS`, and its `STATUS` column shows what is
actually wrong — `Init:CrashLoopBackOff`, `ImagePullBackOff`, `Terminating` —
rather than the pod phase alone. A pod reading `Running` with `0/1` ready is not
working. `events` reports `TYPE`, `REASON`, and the full message; read it before
reaching for logs. Use `-o json` or `-o yaml` only when reasoning needs the whole
object.

Confirm which cluster you are on before the first command of a session, and again
whenever a task spans two environments. A profile is bound to one provider
profile (which owns its managed cluster) or one kubeconfig context; switching
clusters means switching profile, never editing authentication material:

```bash
datus k8s --profile prod version
```

When a container keeps restarting, read its previous instance rather than the
fresh one, and filter server-side instead of dumping everything. For a pod built
from init containers, read them all — the init container is usually the one that
broke:

```bash
datus k8s --profile prod logs worker-abcde -n analytics -c app --previous --tail 300
datus k8s --profile prod logs worker-abcde -n analytics -c app --since 10m
datus k8s --profile prod logs worker-abcde -n analytics --all-containers --tail 100
```

## Wait instead of polling

Never poll with `sleep` and a repeated `get`. Every wait belongs in `wait`, which
takes a deadline, reports what it observes on stderr while it waits, and names the
last observed value when it times out:

```bash
datus k8s --profile prod wait job/daily-etl --for=condition=Complete --timeout=30m -n analytics
datus k8s --profile prod wait pods -l job-name=daily-etl --for=delete --timeout=5m -n analytics
```

Custom resources usually report readiness in their own status fields and never
populate `status.conditions`, so `condition=` cannot express their state. Use a
jsonpath expression against the field the CRD actually sets, and pair it with the
failure state so the wait ends the moment the answer is known:

```bash
datus k8s --profile prod wait flinkdeployment/orders -n analytics \
  --for='jsonpath={.status.jobStatus.state}=RUNNING' \
  --fail-on='jsonpath={.status.jobStatus.state}=FAILED' --timeout=10m
```

`--fail-on` defaults to `jsonpath={.status.error}`, so a resource that reports an
error aborts the wait with that error instead of burning the whole timeout. Pass
`--fail-on=none` to wait regardless, and repeat `--fail-on` for more than one
failure state.

**Waiting on the wrong field is worse than not waiting.** Choose one that is false
while the workload is broken. A field that reports only that the controller
created something — a Flink `jobManagerDeploymentStatus` of `READY`, for instance —
goes true while the job inside is failing, so a wait on it succeeds and the
conclusion drawn from it is wrong. When a resource has both an infrastructure
state and a workload state, wait on the workload state.

Only `.field` and `[index]` steps are supported. Read the object once with
`-o yaml` to learn the real field path before waiting on it. Omit `=VALUE` to
wait until the field merely becomes non-empty. When the resource itself may not
exist yet, chain `--for=create` first.

## Inspect what a container actually sees

`exec` answers questions no manifest can: which JARs are on disk, what the
running process's real environment is, whether a file the config promises is
present. Keep each probe read-only and single-purpose.

```bash
datus k8s --profile prod exec fe-0 -n analytics -c fe -- ls -1 /opt/lib
datus k8s --profile prod exec fe-0 -n analytics -- sh -c 'ls -1 /opt/lib/*.jar | head -50'
```

The command goes after `--`. stdin and TTY are disabled, so interactive shells,
editors, and pagers cannot be used; wrap anything needing a pipeline in
`sh -c '...'`. The pod's exit code becomes the command's exit code, and a
non-zero one is reported as `command terminated with exit code N`.

Do not use `exec` to change a running container. Configuration, files, and
permissions belong in the manifest or image, so that a restart does not silently
undo the fix. When a probe proves something is missing, fix the source.

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
