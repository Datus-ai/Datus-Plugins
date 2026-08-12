# datus-flink-plugin

A skill-only Datus plugin for creating and operating Apache Flink jobs. It
bundles three skills across local validation, SQL packaging, and operation:

| Skill | Use it for |
|---|---|
| `flink-local-dev` | Running a Flink SQL job on the current machine, in an in-process MiniCluster, to validate its logic before it goes anywhere |
| `flink-sql` | Packaging a validated SQL script for Application Mode: build a runner JAR on Flink 1.x or use the built-in `SqlDriver` without `jarURI` on Flink 2.x |
| `flink-k8s-operator` | Building, deploying, and operating the job on the Apache Flink Kubernetes Operator |

The plugin intentionally declares no `datus flink` CLI and no Flink profiles.
`flink-local-dev` drives the Flink SQL Client in the local shell; `flink-sql`
selects and verifies the version-specific SQL application entry point;
`flink-k8s-operator` builds JVM or PyFlink projects, prepares Operator custom
resources, and delegates every Kubernetes workload operation to the separately
installed `datus k8s` plugin.

Each skill is a **single `SKILL.md`** — Datus discovers the skill file only, so a
skill directory cannot ship an `assets/` or `references/` subdirectory. Every
template the skills hand to a project (Operator manifests, Dockerfiles, the SQL
overlays, the local runner script) is inlined in the skill file under a
`### <filename>` heading, and the test suite extracts those blocks to check them.

## Install

Install and configure the Kubernetes plugin first — it is needed for the
deployment stage, not for local validation:

```bash
datus plugin install src:./datus-k8s-plugin
```

Then install this plugin:

```bash
datus plugin install src:./datus-flink-plugin
```

All three skills appear in the Datus skill catalogue. Invoke `flink-local-dev`
when writing or debugging a Flink SQL job, `flink-sql` when packaging it for
production, and `flink-k8s-operator` when creating, upgrading, suspending,
resuming, snapshotting, or diagnosing a FlinkDeployment or FlinkSessionJob.

## Local validation, then production

The intended path for a Flink SQL job:

1. **`flink-local-dev`** — run the script in a MiniCluster inside one JVM. No
   Docker, no Kubernetes, no shared cluster. Sources may be real development
   endpoints; every sink is shadowed with `print`, `blackhole`, or a local
   `file://` table, so the run cannot write to a real system. Judge the output
   rows, changelog kinds, and counts against what the query should produce.
2. **`flink-sql`** — choose the Application Mode entry point, build and test a
   runner JAR for Flink 1.x or select the built-in `SqlDriver` for Flink 2.x,
   then verify the SQL and dependencies in the final image.
3. **`flink-k8s-operator`** — deliver the settled image, render the
   FlinkDeployment, and apply it through `datus k8s`.

The production artifact (`sql/job.sql`) is byte-identical in both stages;
everything local lives in a separate, never-shipped overlay:

```
deploy/flink/<name>/
├── sql/job.sql                 # the artifact — unchanged between stages
├── local/                      # flink-local-dev overlay (git-ignore credentials)
│   ├── local-session.sql       # pins execution.target=local, table.dml-sync=true
│   ├── local-sources.sql       # bounded, read-only dev source shadows
│   ├── local-sinks.sql         # print / blackhole / file:// sink shadows
│   └── run-local-sql.sh        # preflight + guards + SQL Client invocation
└── flinkdeployment.yaml        # flink-k8s-operator output
```

`run-local-sql.sh` fails closed: it refuses to run when the session overlay does
not pin the local execution target and synchronous DML, when an `INSERT` target
has no local shadow, when a sink connector or path is not local, or when an
overlay carrying a credential is tracked by git.

Local validation needs a Flink distribution on the machine (`FLINK_HOME`) whose
minor version matches the production `spec.flinkVersion`, and a JDK that release
supports. It does not need Docker or cluster access.

## Runtime boundary

- Flink Operator installation, CRDs, cluster RBAC, and webhooks are managed by
  the Kubernetes administrator.
- Flink workload reads and writes use `datus k8s` and inherit its namespace
  allowlist and confirmation policy.
- A local run never writes to a production sink, consumer group, CDC slot, or
  checkpoint path, and never builds or pushes a production image.
- Application jobs may package a JAR or Python project into a custom Flink
  image.
- Session jobs normally use an Operator-accessible HTTPS, S3, or HDFS
  artifact URI. A `local://` URI refers to the Operator pod filesystem, not
  merely the Session Cluster image.
- A Flink 1.x SQL application needs a version-matched runner JAR. Flink 2.x can
  use `org.apache.flink.table.runtime.application.SqlDriver` from the system
  classpath with no `jarURI`; connector and filesystem JARs are still required.

The initial schema guidance targets the stable Operator 1.15 API while
discovering the actual `flink.apache.org` resource version from the target
cluster before generating a manifest.

## Develop

```bash
uv run --package datus-flink-plugin pytest datus-flink-plugin
```

The suite renders every template, checks the documented invariants, and drives
`run-local-sql.sh` against a fake Flink distribution to prove each guard rejects
what it claims to reject. The package contains no runtime Python implementation
and never imports or depends on `datus`.
