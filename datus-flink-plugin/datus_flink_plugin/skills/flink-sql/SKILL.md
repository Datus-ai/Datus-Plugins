---
name: flink-sql
description: Prepare and run Apache Flink SQL scripts as Application Mode jobs. Use when packaging or deploying a .sql task, choosing the SQL job entry point, deciding whether a Flink 1.x application needs a version-matched runner JAR or a Flink 2.x application can use the built-in SqlDriver without jarURI, supplying connector/catalog/filesystem dependencies, or handing a validated SQL job to the Flink Kubernetes Operator.
---

# Flink SQL application jobs

Prepare one SQL script as one production Flink application. Validate SQL logic
with `flink-local-dev`, choose the version-specific entry point here, then hand
the settled artifact and job fields to `flink-k8s-operator`. Let that skill own
image delivery, custom resources, deployment, observation, and upgrades.

## Establish the job

Inspect the project and existing deployment files before creating anything.
Resolve these values:

- exact Flink runtime version, not only an Operator enum such as `v1_20`;
- SQL file and the statements that constitute the job;
- streaming or batch runtime mode;
- connector, catalog, format, UDF, and filesystem JARs with exact versions;
- expected terminal state: `RUNNING` for an unbounded job or `FINISHED` for a
  bounded job;
- parallelism, checkpoint/savepoint storage, and upgrade mode;
- image or volume path from which the JobManager can read the SQL script.

Run `flink-local-dev` first. Keep the production SQL byte-identical to the file
validated locally. Local-only `execution.target`, sink shadows, credentials,
and scratch state paths must not enter the production script.

Prefer one Flink job per SQL application. Combine multiple sink writes with
`EXECUTE STATEMENT SET`; separate unbounded `INSERT` statements can create
multiple jobs or prevent later statements from running, which does not fit the
Operator's one-job lifecycle model.

## Select by runtime version

Use the runtime's actual major version:

- **Flink 1.x:** build a small, version-matched SQL runner JAR and submit that
  JAR as the application.
- **Flink 2.x and later:** use Flink's built-in
  `org.apache.flink.table.runtime.application.SqlDriver`; omit `jarURI`.

Do not copy Flink 2.x `flink-table-runtime` or `flink-sql-gateway` JARs into a
1.x image. They are runtime internals, not a backward-compatible runner bundle.

## Flink 1.x: build the main JAR

Flink 1.x has no built-in `SqlDriver`. Create or reuse a JVM application whose
`main()` reads the checked SQL file, creates the correct `TableEnvironment`,
executes statements in order, and propagates parsing, submission, and job
failures. Pin its Flink dependencies to the exact runtime minor version and
mark Flink runtime libraries as `provided`.

The Apache Flink Kubernetes Operator
[`flink-sql-runner-example`](https://github.com/apache/flink-kubernetes-operator/tree/main/examples/flink-sql-runner-example)
is a starting point, not a drop-in production artifact. Its parser is deliberately
simple. Before using a runner, test that it:

- does not split semicolons inside strings, quoted identifiers, or comments;
- either implements `SET`/`RESET` correctly or rejects them and moves those
  settings into `spec.flinkConfiguration`;
- supports the script's `ADD JAR`, catalog, module, and statement-set syntax;
- uses streaming or batch mode as intended;
- waits in a way that exposes bounded-job failure and keeps an unbounded job
  attached to the Application Cluster;
- fails on the first invalid or failed statement.

Use the project wrapper and run tests:

```bash
./mvnw clean verify
./gradlew build
```

Use only the command matching the project. Do not skip tests. Keep connector
JARs separate in `/opt/flink/lib` when possible; do not shade Flink itself into
the runner. Record the runner source revision, artifact SHA-256, entry class,
and compatible Flink version.

Pass these job fields to `flink-k8s-operator` after the runner is in the image.

### job-flink1.yaml

```yaml
job:
  jarURI: local:///opt/flink/usrlib/sql-runner.jar
  entryClass: __RUNNER_ENTRY_CLASS__
  args:
    - --scriptUri
    - file:///opt/flink/usrlib/job.sql
  parallelism: __PARALLELISM__
  upgradeMode: __UPGRADE_MODE__
  state: running
```

Match the arguments to the selected runner's tested CLI contract; `--scriptUri`
is a recommended stable contract, not a flag every existing runner implements.

## Flink 2.x+: use SqlDriver without a job JAR

Flink 2.0 introduced SQL Application Mode. Use this entry class:

```text
org.apache.flink.table.runtime.application.SqlDriver
```

`SqlDriver` lives in the distribution's `flink-table-runtime` JAR on the system
classpath. It accepts exactly one of:

- `--scriptUri <uri>` for a local file, HTTP(S), or a URI backed by a registered
  Flink filesystem;
- `--script <sql>` for inline SQL.

Prefer a local, immutable file in the image or a ConfigMap-backed volume. Inline
SQL makes the custom resource large and easy to expose. Remote HTTP content can
change without a spec change; use a content-addressed URI or fetch and verify it
before startup when immutability matters.

Do not use `org.apache.flink.table.gateway.service.application.ScriptRunner` as
the entry class. It has no `main()`. `SqlDriver` loads it reflectively from the
SQL Gateway JAR and invokes its `run` method.

Before deployment, inspect the final image and require:

- a version-matched `flink-table-runtime` JAR in `/opt/flink/lib`;
- exactly one `flink-sql-gateway*.jar` under `$FLINK_OPT_DIR`, normally
  `/opt/flink/opt`;
- `FLINK_OPT_DIR` set correctly;
- every connector, catalog, format, UDF, and filesystem dependency available
  to both JobManager and TaskManager.

Zero or multiple matching SQL Gateway JARs make `SqlDriver` fail during startup.
The built-in driver removes the custom main JAR build; it does not provide the
job's external dependencies.

For a `FlinkDeployment`, omit `jarURI` and set `spec.mode: standalone`. This is
still Flink Application Mode; `standalone` selects the Operator's Kubernetes
deployment mode, whose application entrypoint can launch a class already on the
system classpath. Do not confuse it with a Flink Session cluster.

Do not leave `spec.mode` at the Operator's default native deployment mode for
this no-JAR path. Native Application Mode in Flink 2.0.x requires exactly one
`pipeline.jars` entry. With no `jarURI`, Operator 1.15 does not provide that
entry; setting `pipeline.jars` or `pipeline.classpaths` to an empty string is not
a workaround. It fails before the JobManager starts. Use `standalone`, or use a
real application JAR when native deployment mode is a hard requirement.

### job-flink2.yaml

```yaml
spec:
  mode: standalone
  job:
    entryClass: org.apache.flink.table.runtime.application.SqlDriver
    args:
      - --scriptUri
      - file:///opt/flink/usrlib/job.sql
    parallelism: __PARALLELISM__
    upgradeMode: __UPGRADE_MODE__
    state: running
```

Do not point `jarURI` at `flink-table-runtime` or `flink-sql-gateway`. The former
is already on the system classpath; the latter contains `ScriptRunner` and the
Gateway service, not the application main.

### Dockerfile.flink2

```dockerfile
ARG FLINK_IMAGE=__FLINK_BASE_IMAGE__
FROM ${FLINK_IMAGE}

USER root
COPY __SQL_FILE__ /opt/flink/usrlib/job.sql
COPY __CONNECTOR_JARS__/ /opt/flink/lib/
RUN chmod 0444 /opt/flink/usrlib/job.sql /opt/flink/lib/*.jar
USER flink
```

Remove the connector `COPY` and its matching `chmod` operand when no additional
JAR is required. Never copy credentials into the image or SQL file.

## Validate the packaged runtime

Before delivery, verify the final image rather than trusting the Dockerfile:

- read `job.sql` back and compare its SHA-256 with the locally validated file;
- list the selected runner or `SqlDriver` class and every connector JAR;
- confirm all Flink, connector, Scala, Java, and table-format versions agree;
- run a bounded smoke SQL through the same entry class and image;
- require a non-zero exit for malformed SQL and a missing connector.

For Flink 2.x, also test the failure cases of zero and duplicate SQL Gateway
JARs. For Flink 1.x, run parser tests containing semicolons in strings, comments,
`SET`, and an `EXECUTE STATEMENT SET`.

## Hand off to the Operator skill

Invoke `flink-k8s-operator` with:

- exact image and Flink version;
- SQL path and its SHA-256;
- the complete `job` fields from the selected version path;
- connector JAR names and versions;
- parallelism, resources, service account, and expected job state;
- checkpoint/savepoint storage and the explicitly chosen upgrade mode.

Do not perform Kubernetes workload operations from this skill. After deployment,
require the expected `RUNNING` or `FINISHED` job state and an empty Operator
error. A successful image build or a ready JobManager is not proof that the SQL
job started.
