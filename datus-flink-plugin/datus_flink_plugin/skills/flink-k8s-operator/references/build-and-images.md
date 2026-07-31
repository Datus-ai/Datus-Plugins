# Build and image delivery

Use this reference when building job code, generating a Dockerfile, publishing
an artifact, or deciding between minikube and a remote registry.

## Contents

1. Version invariants
2. JVM projects
3. PyFlink projects
4. Application images
5. Session artifacts
6. Image delivery

## 1. Version invariants

Derive the Flink version from the project's dependency declarations and
existing runtime files. Confirm it with the user if multiple versions appear.

Keep these values compatible:

- Maven/Gradle Flink dependencies or `apache-flink` Python package
- Docker base image tag
- Operator `spec.flinkVersion` enum, such as Flink `1.20.x` -> `v1_20`
- Scala binary version for Scala artifacts
- Python version supported by the selected PyFlink release

The stable Operator 1.15 reference lists Flink `v1_15` through `v2_2`, but the
cluster's installed CRD and server-side dry-run are authoritative. Do not
silently upgrade a project to make it fit an image.

## 2. JVM projects

Inspect `pom.xml`, `build.gradle`, `build.gradle.kts`, wrapper files, module
layout, shading/assembly configuration, and existing Dockerfiles.

Use the project-native command:

```bash
./mvnw clean verify
./gradlew build
```

Fall back to `mvn clean verify` or `gradle build` only when no wrapper exists
and the system tool is installed. Do not add `-DskipTests`, `-x test`, or
similar flags without an explicit request.

Exclude source, test, original, and dependency-only JARs when locating the job
artifact. If several runnable JARs remain, inspect their manifests and build
configuration, then ask which one is the Flink job. Do not guess.

For Application mode, copy the selected JAR to
`/opt/flink/usrlib/job.jar` and use:

```yaml
job:
  jarURI: local:///opt/flink/usrlib/job.jar
```

Set `entryClass` when the JAR manifest does not unambiguously identify the job
entry point.

## 3. PyFlink projects

Inspect `pyproject.toml`, lockfiles, `requirements*.txt`, existing images,
entry scripts, and the declared `apache-flink` version. Run the project's
normal tests before building.

Use the PyFlink asset only when no suitable Dockerfile exists. Ensure the image
installs a compatible Python runtime and project dependencies. Do not copy
local virtual environments, credentials, caches, or test output into the
image.

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
Flink environments, and the SessionJob artifact rules below still apply.

## 4. Application images

Reuse an existing Dockerfile when it has the correct base version and artifact
layout. Otherwise copy the relevant asset into
`deploy/flink/<name>/Dockerfile`, replace every placeholder, and build from a
context that contains the referenced artifact.

Use a unique tag. Avoid `latest` outside disposable local tests. Record the
image name in the CR next to the generated Dockerfile.

For minikube, set `imagePullPolicy: Never` only after verifying the image is
loaded into that exact minikube profile. For a registry image use
`IfNotPresent` or the project's established policy.

## 5. Session artifacts

FlinkSessionJob is different from Application mode: the Operator process
fetches the artifact and submits it to the Session Cluster.

- Prefer HTTPS.
- Use S3/HDFS only when the Operator allowlist includes the scheme and the
  Operator image has the matching filesystem plugin.
- Treat private/loopback endpoints as unavailable unless an administrator has
  deliberately changed the restricted-host policy.
- Use `local://` only when the path is mounted in the Operator pod itself and
  the scheme is allowed. A file present only in the Session Cluster image is
  not visible to the Operator.

Building a local JAR does not publish it. Ask for the approved artifact
repository and destination before uploading.

## 6. Image delivery

Confirm the selected k8s profile really targets minikube before using local
image commands:

```bash
minikube image build -t <image>:<tag> -f <dockerfile> <context>
minikube image ls
```

For an already built image:

```bash
docker build -t <image>:<tag> -f <dockerfile> <context>
minikube image load <image>:<tag>
```

For remote clusters, require an explicit registry/repository and immutable tag:

```bash
docker build -t <registry>/<repository>:<tag> -f <dockerfile> <context>
docker push <registry>/<repository>:<tag>
```

Do not log in, push, overwrite a tag, or create registry credentials without
the user's authority. Never place registry credentials in the Flink CR; use a
pre-existing Kubernetes imagePullSecret when required.
