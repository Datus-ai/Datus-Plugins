# datus-flink-plugin

A skill-only Datus plugin for creating and operating Apache Flink jobs. The
first runtime integration is the Apache Flink Kubernetes Operator.

The plugin intentionally declares no `datus flink` CLI and no Flink profiles.
Its `flink-k8s-operator` skill builds JVM or PyFlink projects, prepares
Operator custom resources, and delegates every Kubernetes workload operation
to the separately installed `datus k8s` plugin.

## Install

Install and configure the Kubernetes plugin first:

```bash
datus plugin install src:./datus-k8s-plugin
```

Then install this plugin:

```bash
datus plugin install src:./datus-flink-plugin
```

The bundled `flink-k8s-operator` skill appears in the Datus skill catalogue.
Invoke it when creating, upgrading, suspending, resuming, snapshotting, or
diagnosing a FlinkDeployment or FlinkSessionJob.

## Runtime boundary

- Flink Operator installation, CRDs, cluster RBAC, and webhooks are managed by
  the Kubernetes administrator.
- Flink workload reads and writes use `datus k8s` and inherit its namespace
  allowlist and confirmation policy.
- Application jobs may package a JAR or Python project into a custom Flink
  image.
- Session jobs normally use an Operator-accessible HTTPS, S3, or HDFS
  artifact URI. A `local://` URI refers to the Operator pod filesystem, not
  merely the Session Cluster image.

The initial schema guidance targets the stable Operator 1.15 API while
discovering the actual `flink.apache.org` resource version from the target
cluster before generating a manifest.

## Develop

```bash
uv run --package datus-flink-plugin pytest datus-flink-plugin
```

The package contains no runtime Python implementation and never imports or
depends on `datus`.
