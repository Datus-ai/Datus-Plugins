Build and run a bounded Flink-to-Paimon job in the isolated test environment.

Use the installed `flink-sql` and `flink-k8s-operator` skills and perform every
Kubernetes workload operation through `datus k8s --profile e2e`; do not use
kubectl.

Run every `datus k8s` command in its own Bash tool call. Never combine commands
with `;`, `&&`, `||`, pipes, redirects such as `2>&1`, command substitution, or
an inline shell. This workflow is non-interactive and intentionally rejects
shell metacharacters even when each individual command is allowed.

All required runtime values are provided below. Do not search the web or inspect
the filesystem with Bash to discover versions or examples; use the dedicated
file/glob/search tools only if a file check is necessary. The `flink` service
account is already provisioned with the required namespace-scoped permissions.
Do not run impersonation checks with `--as`, which this plugin does not support.

The exact target is:

- Kubernetes namespace: `{{NAMESPACE}}`
- FlinkDeployment name: `flink2paimon-{{RUN_ID}}`
- approved image already loaded in minikube: `{{FLINK_RUNNER_IMAGE}}`
- Flink runtime: `2.0.2`
- FlinkDeployment `spec.flinkVersion`: `v2_0`
- FlinkDeployment `spec.serviceAccount`: `flink`
- Paimon warehouse: `{{PAIMON_WAREHOUSE}}`
- S3 endpoint from inside Kubernetes: `{{MINIO_ENDPOINT}}`
- test-only S3 access key / secret: `{{MINIO_ACCESS_KEY}}` / `{{MINIO_SECRET_KEY}}`
- Paimon database/table: `e2e.events`

Generate a Flink SQL file that:

1. Creates a Paimon catalog for that warehouse and endpoint using path-style S3.
2. Creates database `e2e` and table `events` before inserting any data.
3. Gives `events` exactly these columns in this order: `id BIGINT NOT NULL`,
   `payload STRING`, `source STRING`, with `PRIMARY KEY (id) NOT ENFORCED`.
4. Uses a bounded datagen source with a sequence from 1 through 100.
5. Inserts exactly 100 rows and the literal `datagen` into `source`.

Generate one multi-document YAML containing the SQL ConfigMap and a
`flink.apache.org/v1beta1` FlinkDeployment in Application Mode. Mount the SQL at
`/opt/flink/usrlib/job.sql`. Set `spec.mode: standalone`; this selects the
Operator's standalone Kubernetes deployment mode while the Flink job itself
remains an Application Mode job. Set `spec.job.entryClass` to the Flink 2.x built-in
`org.apache.flink.table.runtime.application.SqlDriver`, and pass exactly these
two job arguments in order: `--scriptUri` and
`file:///opt/flink/usrlib/job.sql`. Omit `spec.job.jarURI` entirely. The approved
image already contains the Paimon dependencies and exactly one SQL Gateway JAR
under `/opt/flink/opt`; `SqlDriver` loads the SQL Gateway `ScriptRunner` from
that distribution JAR. Use `imagePullPolicy: Never`.

Do not run Maven, Gradle, or Docker. Do not build, download, copy, or reference
a custom SQL runner or job JAR. In particular, do not use `sql-runner.jar`,
`ai.datus.e2e.SqlFileRunner`, or any `jarURI`. The environment may contain an
oracle-only verifier JAR; it is not the Flink job main and must not be referenced
by the FlinkDeployment. Do not set `pipeline.jars` or `pipeline.classpaths`,
including to an empty string; the no-JAR `SqlDriver` path depends on
`spec.mode: standalone`, not empty native-mode pipeline configuration.

Write all deliverables under `deploy/flink/{{RUN_ID}}/`:

- `job.sql`
- `flinkdeployment.yaml`
- `run-summary.md`

Apply the YAML, use `datus k8s wait`/`get` to verify the job, and record the
observed resource and job state in `run-summary.md`. Use `logs`/`events` only
for a failed job.

For the success path, wait for `status.jobStatus.state=FINISHED`, then read the
FlinkDeployment once. If the state is `FINISHED` and `status.error` is empty,
immediately write `run-summary.md` and stop. Do not inspect logs, events, pods,
or archived command-output files after that successful result. Use logs/events
only when the wait fails or `status.error` is non-empty. Do not use Bash `grep`,
`wc`, `find`, or similar commands to inspect archived output.
