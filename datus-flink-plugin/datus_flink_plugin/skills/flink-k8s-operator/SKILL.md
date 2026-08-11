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
docs, but the target cluster is authoritative. Production and partially
specified targets require full discovery and server-side validation; the
bounded path below uses its caller-provided values plus one CRD discovery check.

## Bounded, fully specified run

Use this compact path when the caller provides the exact profile, namespace,
served Flink API version, image/artifact, Flink version, service account, and
manifest fields, and explicitly says the isolated environment, image, and RBAC
are already provisioned. Caller-provided values are authoritative:

1. Create all requested deliverables; issue independent file writes together
   when the client supports parallel tool calls.
2. Run one `api-resources --api-group flink.apache.org` check.
3. Apply directly, wait on the requested job state, and read the CR exactly once
   with `get ... -o wide`.
4. On a successful wait and an error-free wide result, write the requested
   summary immediately and stop.

Do not add `version`, `api-versions`, `auth can-i`, server-side dry-run, logs, events,
pod reads, or a second CR read to this path. A successful isolated apply is the
authorization/schema check, and `wait` is fail-closed on `status.error`. If any
required value or provisioning guarantee is missing, use the full preflight and
diagnostic flow below.

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

For production or a partially specified target, select the Kubernetes
environment explicitly:

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

### Delivering into an unmodified base image

When an image cannot be built or pushed — no registry access, no Docker daemon,
or a requirement to keep the official image untouched — deliver the job with init
containers that write into shared `emptyDir` volumes. This works, but the official
image's entrypoint and the Operator's own mounts collide with it in seven specific
ways. Each fails at pod start or at the first SQL statement, and each has one
correct fix. Do not rediscover them one redeploy at a time.

1. **The entrypoint rewrites the config it was given.** `docker-entrypoint.sh`
   runs `config-parser-utils.sh`, which writes back to
   `/opt/flink/conf/flink-conf.yaml`, while the Operator mounts that directory
   from a read-only ConfigMap: `flink-conf.yaml: Read-only file system`. Bypass
   the Docker entrypoint and run the Kubernetes launchers directly —
   `kubernetes-jobmanager.sh kubernetes-application` for the JobManager and
   `kubernetes-taskmanager.sh` for the TaskManager. The entrypoint only translates
   Docker-style environment variables and is not needed under the Operator.

2. **Do not try to make the config directory writable.** Declaring a volume named
   `flink-config-volume` in the pod template collides with the one the Operator
   injects, and Kubernetes rejects the Deployment with
   `Duplicate value: "flink-config-volume"`. Bypass the entrypoint instead.

3. **Artifacts an init container writes must be world-readable.** Init containers
   usually run as root while the Flink container runs as `flink`, so a jar left at
   `0440 root:root` yields `JAR file can't be read '/opt/flink/usrlib/job.jar'`.
   Use `0444` for jars and SQL files and `0555` for directories. Never `chown`
   inside the running container.

4. **`ADD JAR` only accepts a filesystem Flink has registered, and `https` is not
   one**: `UnsupportedFileSystemSchemeException: scheme 'https'`. Download in an
   init container, verify a pinned SHA-256, and reference the result as
   `ADD JAR 'file:///...'`.

5. **`s3://` in `ADD JAR` needs Flink's own S3 filesystem, not the connector's.**
   A table-format artifact such as `paimon-s3` does not register a Flink
   FileSystem. Copy `flink-s3-fs-hadoop-*.jar` out of the image's own
   `/opt/flink/opt` into a shared `/opt/flink/plugins/s3-fs-hadoop/` so both
   JobManager and TaskManager preload it. Prefer `file://` for job artifacts and
   keep `s3://` for checkpoints and table data.

6. **IRSA needs its credentials provider named explicitly.** The Operator injects
   `AWS_ROLE_ARN` and `AWS_WEB_IDENTITY_TOKEN_FILE`, but Hadoop S3A's default
   credential chain does not include the web-identity provider, so S3 access
   fails even with a correct service-account annotation. Set
   `fs.s3a.aws.credentials.provider` to
   `com.amazonaws.auth.WebIdentityTokenCredentialsProvider`, and let checkpoints,
   job artifacts, and table data all use that one role.

7. **A jar loaded through `ADD JAR` cannot see classes inside an isolated plugin
   classloader**, which surfaces as `NoClassDefFoundError` for a Hadoop or
   filesystem class the plugin clearly contains. Prefer copying the table format
   and its Hadoop runtime into `/opt/flink/lib` with an init container so they
   load in the parent classloader. Setting `classloader.resolve-order:
   parent-first` also works but gives up user-code isolation for the whole job;
   record that trade-off if you take it.

One more, when the job is a SQL file executed by a Java entry class: the Table
API does not accept SQL Client statements. `SET 'key' = 'value'` passed to
`TableEnvironment.executeSql()` fails. The runner must recognise `SET` and apply
it through `tableEnv.getConfig().getConfiguration().setString(...)`, executing
only DDL and DML through `executeSql()`. Validate that runner with the
`flink-local-dev` skill before deploying: a MiniCluster run rejects the statement
in seconds, whereas the same mistake costs a full build, upload, and restart
cycle here.

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

The Operator's own permissions are not the job's permissions. In Application mode
the JobManager creates and watches its TaskManagers itself, so its service
account needs, within the namespace: `pods` and `pods/log`, `services` and
`configmaps` (get/list/watch/create/delete/patch/update), and `get`/`list`/`watch`
on `apps/deployments` — without the last one, TaskManager pods are never created
because they cannot inherit their owner reference. A cloud identity binding such
as IRSA grants nothing in Kubernetes; it covers the cloud provider only. Verify
before deploying, impersonating the job's own identity:

```bash
datus k8s --profile <profile> auth can-i list pods -n <namespace> \
  --as system:serviceaccount:<namespace>:<service-account>
```

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

Outside the bounded fully specified path, validate without persisting, then
apply through the same profile:

```bash
datus k8s --profile <profile> apply -f <manifest> -n <namespace> --dry-run server -o yaml
datus k8s --profile <profile> apply -f <manifest> -n <namespace>
```

Show the final manifest and summarize its image, mode, state policy, resources,
and external storage before applying. For a new Session stack, apply the Session
FlinkDeployment first and wait for `status.jobManagerDeploymentStatus=READY`
before applying FlinkSessionJob.

Wait on the status field itself; never poll with `sleep` and a repeated `get`.
Flink CRs report readiness outside `status.conditions`, so use a jsonpath wait —
and wait on the **job** state, not the JobManager's:

```bash
datus k8s --profile <profile> wait flinkdeployment/<name> -n <namespace> \
  --for='jsonpath={.status.jobStatus.state}=RUNNING' \
  --fail-on='jsonpath={.status.jobStatus.state}=FAILED' --timeout=10m
```

`status.jobManagerDeploymentStatus=READY` means the JobManager pod started. It
goes `READY` while the job inside it is failing, so a wait on it reports success
over a dead job — do not treat it as the deployment's outcome. `--fail-on` also
defaults to aborting on a non-empty `status.error`, which is where the Operator
records a job that never started; without it a first-deploy failure looks like a
hang for the whole timeout.

## 7. Observe and diagnose

```bash
datus k8s --profile <profile> get flinkdeployment <name> -n <namespace> -o wide
datus k8s --profile <profile> get pods -n <namespace> -l app=<deployment-name> -o wide
datus k8s --profile <profile> events -n <namespace> --for flinkdeployment/<name>
```

`-o wide` puts `status.lifecycleState` in the STATUS column and the truncated
`status.error` in MESSAGE, and `get pods` shows `READY`/`RESTARTS` plus an
`Init:CrashLoopBackOff`-style status, so the three-layer picture — job, CR, pod —
costs three commands. Read individual fields directly rather than fetching the
whole document:

For a bounded success path, a successful `wait` on the expected job state is
already fail-closed on a non-empty `status.error`. Follow it with exactly one
`get flinkdeployment ... -o wide` to record lifecycle state and message, then
stop. Do not add a second `get` for `status.error` unless the wait failed or the
wide output reports an error. The commands below are for diagnosis when the
bounded success path did not complete:

```bash
datus k8s --profile <profile> get flinkdeployment <name> -n <namespace> \
  -o 'jsonpath={.status.jobStatus.state}'
datus k8s --profile <profile> get flinkdeployment <name> -n <namespace> \
  -o 'jsonpath={.status.error}'
```

Then read the complete CR and inspect `metadata.generation`,
`status.observedGeneration`, `status.lifecycleState`,
`status.jobManagerDeploymentStatus`, `status.jobStatus.{jobId,state}`,
`status.reconciliationStatus`, and `status.error`. For FlinkStateSnapshot inspect
`status.{state,path,failures,resultTimestamp,error}`.

Do not declare success only because the Kubernetes object exists. Require the
JobManager to be ready and, for a job resource, the Flink job to reach its
expected running or terminal state. A non-empty `status.error` takes precedence
over a stale ready field.

Find the actual JobManager pod before reading logs. When the pod delivers its
dependencies through init containers, read those too — a pod stuck at `Pending` is
usually an init container failing, and its log is the only place that says why:

```bash
datus k8s --profile <profile> logs <jobmanager-pod> -n <namespace> --tail 300
datus k8s --profile <profile> logs <jobmanager-pod> -n <namespace> --all-containers --tail 100
```

If the CR never becomes ready, diagnose in this order: Operator reconciliation
error, events, image pull, service account/RBAC, artifact availability,
JobManager logs, TaskManager logs, Flink version mismatch, then checkpoint or
savepoint storage.

Read a restarting container's previous instance with `--previous`, and filter for
`ERROR`, `Exception`, `Caused by`, `NoClassDefFound`, and `Unsupported` rather
than reading a whole log. When a log claims a class, connector, catalog, or
filesystem scheme is missing while the artifact is demonstrably present, stop
redeploying and use the `k8s-jvm-classpath` skill: it separates an absent JAR from
an absent SPI entry, from classloader isolation, from a class that loaded and then
rejected its configuration. Those four produce nearly identical messages and need
different fixes.

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

The Operator reconciles the spec, not the bytes an artifact URI points at.
Overwriting the object behind an unchanged `jarURI` — or behind an init
container's fixed download URL — starts nothing new, and the pod keeps running the
previous build. Either publish each build under an immutable, content-addressed
URI so a new build is a new spec, or bump `restartNonce` explicitly after every
re-upload. Do not conclude a fix failed until you have confirmed which build is
actually running.

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

Order matters while a job has never started successfully. `savepoint` and
`last-state` both ask the Operator to recover state that does not exist yet, which
turns a first-deploy failure into a redeployment loop that hides the original
error. Bring a new job up with `stateless`, confirm the job reaches `RUNNING` and
that checkpoints are actually being written to durable storage, then switch to the
intended mode and verify HA/checkpoint metadata is configured.

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
