Build and run a bounded Flink-to-Paimon job in the isolated test environment.

Use the installed `flink-k8s-operator` skill and perform every Kubernetes workload
operation through `datus k8s --profile e2e`; do not use kubectl.

The exact target is:

- Kubernetes namespace: `{{NAMESPACE}}`
- FlinkDeployment name: `flink2paimon-{{RUN_ID}}`
- approved image already loaded in minikube: `{{FLINK_RUNNER_IMAGE}}`
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
`/opt/flink/usrlib/job.sql`. Run
`local:///opt/flink/usrlib/sql-runner.jar` with entry class
`ai.datus.e2e.SqlFileRunner` and arguments `--sql /opt/flink/usrlib/job.sql`.
Use `imagePullPolicy: Never`.

Write all deliverables under `deploy/flink/{{RUN_ID}}/`:

- `job.sql`
- `flinkdeployment.yaml`
- `run-summary.md`

Apply the YAML, use `datus k8s wait`/`get`/`logs`/`events` to wait for and verify
the job, and record the observed resource and job state in `run-summary.md`.
