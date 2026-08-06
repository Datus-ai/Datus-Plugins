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
datus k8s logs pod-name -n analytics --tail 200 --all-containers
datus k8s events -n analytics
datus k8s top pod -n analytics
datus k8s rollout status deployment/worker -n analytics
```

Tables are shaped per kind, so a failure is visible without fetching the object:
pods carry `READY`/`RESTARTS` and report `Init:CrashLoopBackOff` rather than the
bare phase, events carry `TYPE`/`REASON`/`MESSAGE`, and any other resource falls
back to the conventional status fields (`phase`, `state`, `lifecycleState`) with
`status.error` in the `MESSAGE` column under `-o wide`. To read one field, ask for
it:

```bash
datus k8s get flinkdeployment orders -n analytics -o 'jsonpath={.status.jobStatus.state}'
```

Waiting is a first-class command, so nothing needs to poll with `sleep`. Custom
resources that report readiness outside `status.conditions` are covered by a
jsonpath condition, and `--fail-on` ends the wait as soon as the resource reports
failure instead of burning the timeout:

```bash
datus k8s wait job/daily-etl --for=condition=Complete --timeout=30m -n analytics
datus k8s wait flinkdeployment/orders -n analytics \
  --for='jsonpath={.status.jobStatus.state}=RUNNING' \
  --fail-on='jsonpath={.status.jobStatus.state}=FAILED' --timeout=10m
```

`--fail-on` defaults to `jsonpath={.status.error}`; `--fail-on=none` opts out.
While waiting, each newly observed value is reported on stderr and the timeout
message names the last one, so a wait is never a silent block.

State-changing commands (`create`, `apply`, `delete`, `patch`, `scale`,
`rollout restart`, `label`, and `annotate`) always require agent confirmation.
So does `exec`, which runs one non-interactive command in a pod so a diagnostic
probe can read what a running process actually sees:

```bash
datus k8s exec fe-0 -n analytics -c fe -- sh -c 'ls -1 /opt/lib/*.jar'
```

stdin and TTY are always disabled and the pod's exit code is propagated, so
`exec` cannot be used as a shell.

This is a deliberate kubectl-style subset. It does not support cluster-scoped
resources, `-A`, interactive exec, attach/cp, port-forward/proxy, Kustomize,
kubeconfig mutation, Go templates, custom-columns, or kubectl plugins. The
JSONPath surface — `-o jsonpath=` and `wait --for=`/`--fail-on=` — accepts
`.field` and `[index]` steps only; filters, wildcards, and ranges are rejected
rather than silently mis-evaluated.

## Develop

```bash
uv run --package datus-k8s-plugin pytest datus-k8s-plugin
```

The package never imports `datus`; its complete plugin contract is declared in
`datus_k8s_plugin/datus-plugin.yml`.
