# Flink Kubernetes Operator CRs

Use this reference before generating, applying, patching, snapshotting,
recovering, or deleting Operator resources.

## Contents

1. Sources and compatibility
2. Resource selection
3. Status interpretation
4. Lifecycle patches
5. Snapshots and recovery
6. Deletion and failure handling

## 1. Sources and compatibility

The templates follow the stable Apache Flink Kubernetes Operator 1.15
documentation:

- [Custom resource overview](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/overview/)
- [Custom resource reference](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/reference/)
- [Job lifecycle management](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/job-management/)
- [Snapshots](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/custom-resource/snapshots/)
- [Compatibility guarantees](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/docs/operations/compatibility/)

Operator 1.15 serves `flink.apache.org/v1beta1`, but always discover the
version from the target cluster. v1beta1 resources are backward-compatible
from Operator 1.0 onward; individual kinds and newer fields can still be
absent. Let server-side dry-run reject an unsupported schema before apply.

## 2. Resource selection

- Use FlinkDeployment with `spec.job` for an Application cluster.
- Use FlinkDeployment without `spec.job` for a Session Cluster.
- Use FlinkSessionJob with `spec.deploymentName` for each job submitted to a
  Session Cluster.
- Use FlinkStateSnapshot with `spec.jobReference` for a manual savepoint or
  checkpoint.

Keep all linked Session resources in the same namespace. Verify a referenced
Session Cluster exists and reports JobManager `READY` before creating a
FlinkSessionJob.

Treat these fields as a coupled set:

- `spec.image`
- `spec.flinkVersion`
- `spec.serviceAccount`
- `spec.flinkConfiguration`
- `spec.jobManager.resource`
- `spec.taskManager.resource`
- `spec.job.jarURI`, entry class, arguments, parallelism, state, upgrade mode

Use Kubernetes Secrets for credentials referenced by pod templates or Flink
configuration. Do not embed credential values.

## 3. Status interpretation

Read the complete CR with `-o yaml` or `-o json`. Important fields include:

- `metadata.generation`
- `status.observedGeneration`
- `status.lifecycleState`
- `status.jobManagerDeploymentStatus`
- `status.jobStatus.jobId`
- `status.jobStatus.state`
- `status.reconciliationStatus`
- `status.error`

For FlinkStateSnapshot inspect:

- `status.state`
- `status.path`
- `status.failures`
- `status.resultTimestamp`
- `status.error`

Do not declare success only because the Kubernetes object exists. Require the
JobManager to be ready and, for a job resource, the Flink job to reach its
expected running or terminal state. A non-empty `status.error` takes
precedence over a stale ready field.

## 4. Lifecycle patches

Use JSON merge patch for CRDs.

Suspend:

```bash
datus k8s --profile <profile> patch flinkdeployment <name> \
  -n <namespace> --type merge -p '{"spec":{"job":{"state":"suspended"}}}'
```

Resume:

```bash
datus k8s --profile <profile> patch flinkdeployment <name> \
  -n <namespace> --type merge -p '{"spec":{"job":{"state":"running"}}}'
```

The same `spec.job.state` patch applies to FlinkSessionJob.

Restart without another spec change by changing the top-level nonce:

```bash
datus k8s --profile <profile> patch flinkdeployment <name> \
  -n <namespace> --type merge -p '{"spec":{"restartNonce":<new-integer>}}'
```

Read the current nonce first and use a different integer. Never reuse a nonce
and never patch status.

For an image, arguments, parallelism, or resource upgrade, edit the
source-controlled manifest and use Server-Side Apply rather than assembling a
large inline patch. Preserve the selected `spec.job.upgradeMode`:

- `stateless`: no state continuity
- `savepoint`: stop with a savepoint and restore; requires a healthy running
  job and durable savepoint storage
- `last-state`: restore from the latest checkpoint/savepoint; requires durable
  HA/checkpoint metadata

Do not claim an exactly-once/stateful upgrade when these prerequisites are not
verified.

## 5. Snapshots and recovery

Prefer FlinkStateSnapshot when the kind is served. Create a new resource for
each requested snapshot and link it to FlinkDeployment or FlinkSessionJob.
Set `spec.savepoint.disposeOnDelete: false` by default so deleting the CR does
not delete backup data.

Wait for `status.state=COMPLETED`, require a non-empty durable `status.path`,
and report `status.error` on failure. Do not delete or upgrade the source job
while a required snapshot is pending.

Treat every Snapshot CR as one immutable attempt. If it reaches `FAILED`, keep
its error for diagnosis and create a new uniquely named Snapshot only after
the source job is again `RUNNING/STABLE`. Errors such as `Checkpoint
Coordinator is suspending` require checking the Flink runtime/operator
compatibility and job lifecycle; blind retries are not a recovery strategy.

To start or recover from an existing snapshot, verify the path, storage
credentials, job compatibility, and ownership. Set
`spec.job.initialSavepointPath` before the first deployment. For an existing
job, follow the Operator's savepoint redeploy procedure and change
`savepointRedeployNonce`; warn that rollback is not available after that
redeployment.

Legacy `savepointTriggerNonce` is deprecated in Operator 1.15. Do not silently
fall back when FlinkStateSnapshot is missing; explain the Operator capability
gap and obtain an explicit decision.

## 6. Deletion and failure handling

For a stateful job:

1. Confirm whether a final savepoint is required.
2. Wait for the savepoint to complete and record its path.
3. Delete the FlinkSessionJob or Application FlinkDeployment.
4. Verify deletion and inspect events on timeout.

Delete every SessionJob before deleting its Session Cluster. If the user asks
to remove the cluster first, state that all jobs on it will stop and require
explicit confirmation.

Use:

```bash
datus k8s --profile <profile> delete flinksessionjob <name> -n <namespace>
datus k8s --profile <profile> delete flinkdeployment <name> -n <namespace>
```

Do not remove finalizers manually. If deletion stalls, inspect the CR, Operator
events/logs, Session Cluster health, and configured delete/savepoint policy
before considering administrator intervention.
