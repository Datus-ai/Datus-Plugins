---
name: flink-k8s-operator
description: Build, deploy, operate, upgrade, snapshot, and troubleshoot Apache Flink jobs running on the Flink Kubernetes Operator. Use for FlinkDeployment application clusters, session clusters, FlinkSessionJob, FlinkStateSnapshot, Java/Scala Maven or Gradle projects, PyFlink projects, minikube image loading, remote registry publishing, job status, logs, events, suspend/resume, restart, stateful upgrades, recovery, or deletion. Delegate every Kubernetes workload operation to `datus k8s`.
---

# Flink Kubernetes Operator

Operate Flink jobs through Operator custom resources. Use `datus k8s` for
resource discovery, validation, apply, status, logs, events, patch, and delete.
Do not use `kubectl` for workload operations and do not install the Operator,
CRDs, webhooks, or cluster RBAC.

The templates below follow the stable Apache Flink Kubernetes Operator 1.15
docs, but the target cluster is authoritative — always discover the served API
version and let server-side dry-run reject an unsupported schema before apply.

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

For a job whose logic is Flink SQL, validate it with the `flink-local-dev`
skill before deploying it here.

## 2. Run fail-closed preflight

Always select the Kubernetes environment explicitly:

```bash
datus k8s --profile <profile> version
datus k8s --profile <profile> api-resources --api-group flink.apache.org
datus k8s --profile <profile> api-versions
```

Require namespaced `FlinkDeployment` and, for Session mode, `FlinkSessionJob`.
Require `FlinkStateSnapshot` before using the snapshot workflow. Use the
discovered served API version; do not assume the version from a bundled
example. Operator 1.15 serves `flink.apache.org/v1beta1`, and v1beta1 resources
are backward-compatible from Operator 1.0 onward, but individual kinds and newer
fields can still be absent.

Verify access for the selected namespace:

```bash
datus k8s --profile <profile> auth can-i get flinkdeployments -n <namespace>
datus k8s --profile <profile> auth can-i create flinkdeployments -n <namespace>
```

Use the plural printed by `api-resources`. Also check create/patch/delete for
every kind the requested workflow changes.

Stop with an actionable message when the k8s plugin is absent or unconfigured,
the namespace is outside its allowlist, a CRD is missing, or authorization is
denied. Ask an administrator to install or reconfigure the Operator; do not work
around namespace or cluster-scope guardrails.

## 3. Keep the version invariants

Derive the Flink version from the project's dependency declarations and existing
runtime files. Confirm it with the user if multiple versions appear. Keep these
values compatible:

- Maven/Gradle Flink dependencies or the `apache-flink` Python package
- Docker base image tag
- Operator `spec.flinkVersion` enum, such as Flink `1.20.x` -> `v1_20`
- Scala binary version for Scala artifacts
- Python version supported by the selected PyFlink release

The Operator 1.15 reference lists Flink `v1_15` through `v2_2`. Do not silently
upgrade a project to make it fit an image.

## 4. Build the artifact

Prefer project wrappers over system tools. Run the project's tests by default;
skip them only when the user explicitly requests it.

**JVM projects.** Inspect `pom.xml`, `build.gradle`, `build.gradle.kts`, wrapper
files, module layout, shading/assembly configuration, and existing Dockerfiles.

```bash
./mvnw clean verify
./gradlew build
```

Fall back to `mvn clean verify` or `gradle build` only when no wrapper exists and
the system tool is installed. Do not add `-DskipTests`, `-x test`, or similar
flags without an explicit request.

Exclude source, test, original, and dependency-only JARs when locating the job
artifact. If several runnable JARs remain, inspect their manifests and build
configuration, then ask which one is the Flink job. Do not guess.

For Application mode, copy the selected JAR to `/opt/flink/usrlib/job.jar` and
use `jarURI: local:///opt/flink/usrlib/job.jar`. Set `entryClass` when the JAR
manifest does not unambiguously identify the job entry point.

**PyFlink projects.** Inspect `pyproject.toml`, lockfiles, `requirements*.txt`,
existing images, entry scripts, and the declared `apache-flink` version. Run the
project's normal tests before building. Ensure the image installs a compatible
Python runtime and project dependencies; never copy local virtual environments,
credentials, caches, or test output into an image.

Locate the Python driver JAR in the actual image rather than guessing its
versioned filename:

```bash
docker run --rm <image> sh -c 'ls -1 /opt/flink/opt/flink-python*.jar'
```

Use that `local://` path, the Python driver entry class, and put Flink's Python
arguments before user arguments:

```yaml
job:
  jarURI: local:///opt/flink/opt/<discovered-flink-python-jar>
  entryClass: org.apache.flink.client.python.PythonDriver
  args:
    - -pyclientexec
    - /usr/bin/python3
    - -py
    - /opt/flink/usrlib/python/main.py
    - --user-argument
    - value
```

Application mode is the default for locally packaged PyFlink jobs. For Session
mode, the Operator and Session Cluster must both have compatible Python and
Flink environments, and the SessionJob artifact rules in §5 still apply.

Reuse an existing Dockerfile when it has the correct base version and artifact
layout. Otherwise write one of these into `deploy/flink/<name>/Dockerfile`,
replace every placeholder, and build from a context that contains the referenced
artifact.

### Dockerfile.jvm

```dockerfile
ARG FLINK_IMAGE=__FLINK_BASE_IMAGE__
FROM ${FLINK_IMAGE}

USER root
RUN mkdir -p /opt/flink/usrlib
COPY __JOB_JAR__ /opt/flink/usrlib/job.jar
RUN chown -R flink:flink /opt/flink/usrlib
USER flink
```

### Dockerfile.pyflink

```dockerfile
ARG FLINK_IMAGE=__FLINK_BASE_IMAGE__
FROM ${FLINK_IMAGE}

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY __PYTHON_PROJECT__/ /opt/flink/usrlib/python/
RUN if [ -f /opt/flink/usrlib/python/requirements.txt ]; then \
      python3 -m pip install --no-cache-dir -r /opt/flink/usrlib/python/requirements.txt; \
    fi \
    && chown -R flink:flink /opt/flink/usrlib/python
USER flink
```

Use a unique tag; avoid `latest` outside disposable local tests. Record the image
name next to the generated Dockerfile.

## 5. Deliver the image or artifact

Confirm the selected k8s profile really targets minikube before using local image
commands:

```bash
minikube image build -t <image>:<tag> -f <dockerfile> <context>
minikube image ls
```

For an already built image:

```bash
docker build -t <image>:<tag> -f <dockerfile> <context>
minikube image load <image>:<tag>
```

For remote clusters, require an explicit registry/repository and an immutable
tag or digest:

```bash
docker build -t <registry>/<repository>:<tag> -f <dockerfile> <context>
docker push <registry>/<repository>:<tag>
```

Ask before pushing an image or publishing an artifact. Do not log in, overwrite a
tag, or create registry credentials without the user's authority. Never place
registry credentials in a Flink CR; use a pre-existing Kubernetes
imagePullSecret. Set `imagePullPolicy: Never` only after verifying the image is
loaded into that exact minikube profile; for a registry image use `IfNotPresent`
or the project's established policy.

**Session artifacts are different from Application mode**: the Operator process
fetches the artifact and submits it to the Session Cluster.

- Prefer HTTPS.
- Use S3/HDFS only when the Operator allowlist includes the scheme and the
  Operator image has the matching filesystem plugin.
- Treat private/loopback endpoints as unavailable unless an administrator has
  deliberately changed the restricted-host policy.
- Use `local://` only when the path is mounted in the Operator pod itself. A file
  present only in the Session Cluster image is not visible to the Operator.

Building a local JAR does not publish it. Ask for the approved artifact
repository and destination before uploading.

## 6. Render the custom resources

Resource selection:

- FlinkDeployment with `spec.job` — Application cluster
- FlinkDeployment without `spec.job` — Session Cluster
- FlinkSessionJob with `spec.deploymentName` — one job on a Session Cluster
- FlinkStateSnapshot with `spec.jobReference` — manual savepoint or checkpoint

Keep all linked Session resources in the same namespace, and verify the
referenced Session Cluster exists and reports JobManager `READY` before creating
a FlinkSessionJob. Treat these fields as a coupled set: `spec.image`,
`spec.flinkVersion`, `spec.serviceAccount`, `spec.flinkConfiguration`,
`spec.jobManager.resource`, `spec.taskManager.resource`, and
`spec.job.{jarURI,entryClass,args,parallelism,state,upgradeMode}`.

Copy the smallest matching template, replace every `__PLACEHOLDER__`, remove
fields the discovered CRD schema does not support, and keep the namespace
explicit. Confirm the named service account exists. Never put registry
credentials, object-store secrets, passwords, or tokens directly in a manifest;
reference existing Kubernetes Secrets.

### flinkdeployment-application.yaml

```yaml
apiVersion: __FLINK_API_VERSION__
kind: FlinkDeployment
metadata:
  name: __NAME__
  namespace: __NAMESPACE__
spec:
  image: __IMAGE__
  imagePullPolicy: __IMAGE_PULL_POLICY__
  flinkVersion: __FLINK_VERSION__
  serviceAccount: __SERVICE_ACCOUNT__
  flinkConfiguration:
    taskmanager.numberOfTaskSlots: "__TASK_SLOTS__"
  jobManager:
    resource:
      memory: __JOB_MANAGER_MEMORY__
      cpu: __JOB_MANAGER_CPU__
  taskManager:
    resource:
      memory: __TASK_MANAGER_MEMORY__
      cpu: __TASK_MANAGER_CPU__
  job:
    jarURI: __JOB_URI__
    entryClass: __ENTRY_CLASS__
    args: []
    parallelism: __PARALLELISM__
    upgradeMode: __UPGRADE_MODE__
    state: running
```

### flinkdeployment-session.yaml

```yaml
apiVersion: __FLINK_API_VERSION__
kind: FlinkDeployment
metadata:
  name: __SESSION_CLUSTER_NAME__
  namespace: __NAMESPACE__
spec:
  image: __IMAGE__
  imagePullPolicy: __IMAGE_PULL_POLICY__
  flinkVersion: __FLINK_VERSION__
  serviceAccount: __SERVICE_ACCOUNT__
  flinkConfiguration:
    taskmanager.numberOfTaskSlots: "__TASK_SLOTS__"
  jobManager:
    resource:
      memory: __JOB_MANAGER_MEMORY__
      cpu: __JOB_MANAGER_CPU__
  taskManager:
    resource:
      memory: __TASK_MANAGER_MEMORY__
      cpu: __TASK_MANAGER_CPU__
```

### flinksessionjob.yaml

```yaml
apiVersion: __FLINK_API_VERSION__
kind: FlinkSessionJob
metadata:
  name: __JOB_NAME__
  namespace: __NAMESPACE__
spec:
  deploymentName: __SESSION_CLUSTER_NAME__
  job:
    jarURI: __OPERATOR_ACCESSIBLE_JOB_URI__
    entryClass: __ENTRY_CLASS__
    args: []
    parallelism: __PARALLELISM__
    upgradeMode: __UPGRADE_MODE__
    state: running
```

### flinkstatesnapshot.yaml

```yaml
apiVersion: __FLINK_API_VERSION__
kind: FlinkStateSnapshot
metadata:
  name: __SNAPSHOT_NAME__
  namespace: __NAMESPACE__
spec:
  jobReference:
    kind: __FlinkDeployment_OR_FlinkSessionJob__
    name: __JOB_RESOURCE_NAME__
  savepoint:
    disposeOnDelete: false
    formatType: CANONICAL
```

Validate without persisting, then apply through the same profile:

```bash
datus k8s --profile <profile> apply -f <manifest> -n <namespace> --dry-run server -o yaml
datus k8s --profile <profile> apply -f <manifest> -n <namespace>
```

Show the final manifest and summarize its image, mode, state policy, resources,
and external storage before applying. For a new Session stack, apply the Session
FlinkDeployment first and wait for `status.jobManagerDeploymentStatus=READY`
before applying FlinkSessionJob.

## 7. Observe and diagnose

```bash
datus k8s --profile <profile> get flinkdeployment <name> -n <namespace> -o yaml
datus k8s --profile <profile> get flinksessionjob <name> -n <namespace> -o yaml
datus k8s --profile <profile> get pods -n <namespace> -l app=<deployment-name> -o wide
datus k8s --profile <profile> events -n <namespace> --for flinkdeployment/<name>
```

Read the complete CR and inspect `metadata.generation`,
`status.observedGeneration`, `status.lifecycleState`,
`status.jobManagerDeploymentStatus`, `status.jobStatus.{jobId,state}`,
`status.reconciliationStatus`, and `status.error`. For FlinkStateSnapshot inspect
`status.{state,path,failures,resultTimestamp,error}`.

Do not declare success only because the Kubernetes object exists. Require the
JobManager to be ready and, for a job resource, the Flink job to reach its
expected running or terminal state. A non-empty `status.error` takes precedence
over a stale ready field.

Find the actual JobManager pod before reading logs:

```bash
datus k8s --profile <profile> logs <jobmanager-pod> -n <namespace> --tail 300
```

If the CR never becomes ready, diagnose in this order: Operator reconciliation
error, events, image pull, service account/RBAC, artifact availability,
JobManager logs, TaskManager logs, Flink version mismatch, then checkpoint or
savepoint storage.

## 8. Change the lifecycle safely

Use JSON merge patch for Operator CRDs; strategic merge patch is not supported
for arbitrary CRDs.

```bash
# suspend (same field works for FlinkSessionJob); resume with "running"
datus k8s --profile <profile> patch flinkdeployment <name> \
  -n <namespace> --type merge -p '{"spec":{"job":{"state":"suspended"}}}'

# restart without another spec change
datus k8s --profile <profile> patch flinkdeployment <name> \
  -n <namespace> --type merge -p '{"spec":{"restartNonce":<new-integer>}}'
```

Read the current nonce first and use a different integer. Never reuse a nonce and
never patch status.

For an image, arguments, parallelism, or resource upgrade, edit the
source-controlled manifest and apply it rather than assembling a large inline
patch. Preserve the explicitly chosen `spec.job.upgradeMode`, and verify its
prerequisites first:

- `stateless` — no state continuity
- `savepoint` — stop with a savepoint and restore; requires a healthy running job
  and durable savepoint storage
- `last-state` — restore from the latest checkpoint/savepoint; requires durable
  HA/checkpoint metadata

Do not claim an exactly-once or stateful upgrade when these prerequisites are not
verified.

## 9. Snapshot and recover

Prefer FlinkStateSnapshot when the kind is served. Create a new, uniquely named
resource for each requested snapshot and link it to its FlinkDeployment or
FlinkSessionJob. Keep `disposeOnDelete: false` unless the user explicitly
requests disposal.

Wait for `status.state=COMPLETED`, require a non-empty durable `status.path`, and
report `status.error` on failure. Do not delete or upgrade the source job while a
required snapshot is pending.

Treat every Snapshot CR as one immutable attempt. If it reaches `FAILED`, keep its
error for diagnosis and create a new snapshot only after the source job is again
`RUNNING/STABLE`. Errors such as `Checkpoint Coordinator is suspending` require
checking Flink runtime/Operator compatibility and the job lifecycle; blind
retries are not a recovery strategy.

To start or recover from an existing snapshot, verify the path, storage
credentials, job compatibility, and ownership. Set
`spec.job.initialSavepointPath` before the first deployment. For an existing job,
follow the Operator's savepoint redeploy procedure and change
`savepointRedeployNonce`; warn that rollback is not available after that
redeployment.

Legacy `savepointTriggerNonce` is deprecated in Operator 1.15. Do not silently
fall back when FlinkStateSnapshot is missing; explain the Operator capability gap
and obtain an explicit decision.

## 10. Delete

For a stateful job:

1. Confirm whether a final savepoint is required.
2. Wait for the savepoint to complete and record its path.
3. Delete the FlinkSessionJob or Application FlinkDeployment.
4. Verify deletion and inspect events on timeout.

```bash
datus k8s --profile <profile> delete flinksessionjob <name> -n <namespace>
datus k8s --profile <profile> delete flinkdeployment <name> -n <namespace>
```

Delete every SessionJob before deleting its Session Cluster. If the user asks to
remove the cluster first, state that all jobs on it will stop and require
explicit confirmation. Do not use Kubernetes rollout restart/status for Flink
CRs, and do not remove finalizers manually. If deletion stalls, inspect the CR,
Operator events/logs, Session Cluster health, and the configured
delete/savepoint policy before considering administrator intervention.

## Sources

- [Custom resource overview](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/overview/)
- [Custom resource reference](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/reference/)
- [Job lifecycle management](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/job-management/)
- [Snapshots](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/snapshots/)
- [Compatibility guarantees](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/operations/compatibility/)
