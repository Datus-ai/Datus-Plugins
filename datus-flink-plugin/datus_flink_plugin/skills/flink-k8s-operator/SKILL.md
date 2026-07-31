---
name: flink-k8s-operator
description: Build, deploy, operate, upgrade, snapshot, and troubleshoot Apache Flink jobs running on the Flink Kubernetes Operator. Use for FlinkDeployment application clusters, session clusters, FlinkSessionJob, FlinkStateSnapshot, Java/Scala Maven or Gradle projects, PyFlink projects, minikube image loading, remote registry publishing, job status, logs, events, suspend/resume, restart, stateful upgrades, recovery, or deletion. Delegate every Kubernetes workload operation to `datus k8s`.
---

# Flink Kubernetes Operator

Operate Flink jobs through Operator custom resources. Use `datus k8s` for
resource discovery, validation, apply, status, logs, events, patch, and delete.
Do not use `kubectl` for workload operations and do not install the Operator,
CRDs, webhooks, or cluster RBAC.

Read the bundled material only when relevant:

- Read [operator-crds.md](references/operator-crds.md) before creating or
  changing CRs, diagnosing reconciliation, taking snapshots, or recovering a
  job.
- Read [build-and-images.md](references/build-and-images.md) before building a
  Maven, Gradle, or PyFlink project or delivering an image/artifact.
- Copy the files under `assets/` only as starting points. Replace every
  `__PLACEHOLDER__`, remove unused optional fields, and validate the result.

## 1. Establish the target

Collect or derive these values before changing files:

- Kubernetes profile and allowed namespace
- Application or Session mode
- resource name and project output directory
- Flink runtime version and image
- JVM JAR plus entry class, or PyFlink entry script and arguments
- parallelism, task slots, JobManager/TaskManager resources, service account
- stateless, savepoint, or last-state upgrade mode
- checkpoint/savepoint storage for stateful jobs
- minikube-local or remote-registry image delivery

Use `deploy/flink/<resource-name>/` by default. Inspect existing manifests and
Dockerfiles first. Never overwrite them silently.

For stateful jobs, do not choose an upgrade mode implicitly. Ask the user when
the project or existing manifest does not make the required state guarantees
clear.

## 2. Run fail-closed preflight

Always select the Kubernetes environment explicitly:

```bash
datus k8s --profile <profile> version
datus k8s --profile <profile> api-resources --api-group flink.apache.org
datus k8s --profile <profile> api-versions
```

Require namespaced `FlinkDeployment` and, for Session mode,
`FlinkSessionJob`. Require `FlinkStateSnapshot` before using the snapshot
workflow. Use the discovered served API version; do not assume the version
from a bundled example.

Verify access for the selected namespace:

```bash
datus k8s --profile <profile> auth can-i get flinkdeployments -n <namespace>
datus k8s --profile <profile> auth can-i create flinkdeployments -n <namespace>
```

Use the plural printed by `api-resources`. Also check create/patch/delete for
every kind the requested workflow changes.
Stop with an actionable message when the k8s plugin is absent or unconfigured,
the namespace is outside its allowlist, a CRD is missing, or authorization is
denied. Ask an administrator to install or reconfigure the Operator; do not
work around namespace or cluster-scope guardrails.

## 3. Build and deliver the artifact

Follow [build-and-images.md](references/build-and-images.md).

- Prefer project wrappers (`mvnw`, `gradlew`) over system tools.
- Run the project's tests by default. Skip them only when the user explicitly
  requests it.
- Ensure the project dependency version, base image, `spec.flinkVersion`, and
  PyFlink package version agree.
- Use an immutable image tag or digest for remote clusters.
- Build/load directly into minikube only when the selected k8s context is the
  intended local cluster.
- Ask before pushing an image or publishing an artifact.

Application jobs may use `local:///opt/flink/usrlib/job.jar` from their own
image. Session jobs normally require an Operator-accessible HTTPS URI. S3,
HDFS, and other schemes require both the Operator allowlist and the matching
filesystem plugin. A `local://` SessionJob URI is valid only when the Operator
pod itself can read that path; a JAR present only in the Session Cluster image
is insufficient.

## 4. Render and create the CRs

Choose the smallest matching asset:

- `assets/flinkdeployment-application.yaml` for an Application cluster
- `assets/flinkdeployment-session.yaml` for a Session cluster
- `assets/flinksessionjob.yaml` for a job on an existing Session cluster
- `assets/flinkstatesnapshot.yaml` for a manual savepoint

Keep the namespace explicit and consistent. Confirm the named service account
exists. Remove fields that are not supported by the discovered CRD schema.
Never put registry credentials, object-store secrets, passwords, or tokens
directly in a manifest; reference existing Kubernetes Secrets.

Validate without persisting:

```bash
datus k8s --profile <profile> apply -f <manifest> -n <namespace> --dry-run server -o yaml
```

Show the final manifest and summarize its image, mode, state policy, resources,
and external storage. Then apply it through the same profile:

```bash
datus k8s --profile <profile> apply -f <manifest> -n <namespace>
```

For a new Session stack, apply the Session FlinkDeployment first and wait for
`status.jobManagerDeploymentStatus=READY` before applying FlinkSessionJob.

## 5. Observe and diagnose

Use structured output for reasoning:

```bash
datus k8s --profile <profile> get flinkdeployment <name> -n <namespace> -o yaml
datus k8s --profile <profile> get flinksessionjob <name> -n <namespace> -o yaml
datus k8s --profile <profile> get pods -n <namespace> -l app=<deployment-name> -o wide
datus k8s --profile <profile> events -n <namespace> --for flinkdeployment/<name>
```

Inspect lifecycle state, reconciliation state, JobManager deployment status,
job state/ID, observed generation, and `status.error`. Find the actual
JobManager pod before reading logs:

```bash
datus k8s --profile <profile> logs <jobmanager-pod> -n <namespace> --tail 300
```

If the CR never becomes ready, diagnose in this order: Operator reconciliation
error, events, image pull, service account/RBAC, artifact availability,
JobManager logs, TaskManager logs, Flink version mismatch, then checkpoint or
savepoint storage.

## 6. Change lifecycle safely

Read the exact merge-patch examples and status gates in
[operator-crds.md](references/operator-crds.md).

- Use `datus k8s patch ... --type merge` for Operator CRDs. Strategic merge
  patch is not supported for arbitrary CRDs.
- Suspend by setting `spec.job.state: suspended`; resume by setting it to
  `running`.
- Restart without another spec change by changing top-level
  `spec.restartNonce` to a new integer.
- Upgrade by changing the image/job spec and preserving the explicitly chosen
  `spec.job.upgradeMode`. Verify the required HA/checkpoint/savepoint
  prerequisites first.
- Trigger a manual savepoint with a new FlinkStateSnapshot CR. Set
  `disposeOnDelete: false` unless the user explicitly requests disposal.
- Recover from an existing savepoint only after verifying its durable path and
  compatibility with the new job.

Before deleting a stateful job, offer a savepoint and wait for it to complete.
Delete SessionJobs before their Session Cluster unless the user explicitly
accepts terminating every job on that cluster.

Use `datus k8s delete`; do not use Kubernetes rollout restart/status for Flink
CRs.
