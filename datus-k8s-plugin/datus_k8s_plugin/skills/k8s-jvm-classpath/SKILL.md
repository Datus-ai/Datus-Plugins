---
name: k8s-jvm-classpath
description: Diagnose classpath, ServiceLoader/SPI, and classloader failures in a JVM service running on Kubernetes — NoClassDefFoundError, ClassNotFoundException, "could not find a file io implementation for scheme", UnsupportedSchemeException, missing connector/catalog/FileIO/FileSystem factory, plugin JAR present but not discovered, or a version that does not match the deployed image. Use for Flink, Paimon, Iceberg, Hive, Spark, Trino, StarRocks, and other JVM data services; probes read-only through `datus k8s exec`.
---

# JVM classpath and SPI failures on Kubernetes

A JVM service reporting "class not found", "no implementation for scheme", or
"factory not found" has four distinct causes that produce nearly identical
messages. Guessing wastes a redeploy each time. Identify which one it is with
read-only probes first, then fix the image or manifest — never the running pod.

| Cause | What is true | Fix belongs in |
|---|---|---|
| JAR absent | the file is not in any classpath directory | image or init container |
| SPI file absent | the JAR is there, `META-INF/services/<interface>` is not | the artifact itself |
| Classloader isolation | both are there, but the caller's loader cannot see them | plugin/lib placement or resolve order |
| Option validation | the class loaded and then rejected its configuration | the resource's configuration |

The last one is the trap: it reports a missing implementation while the
implementation is present and simply refused to initialise.

## 1. Read the real error, not the summary

```bash
datus k8s --profile <profile> logs <pod> -n <namespace> -c <container> --tail 400
datus k8s --profile <profile> logs <pod> -n <namespace> -c <container> --previous --tail 400
datus k8s --profile <profile> logs <pod> -n <namespace> --all-containers --tail 100
```

Keep the whole `Caused by` chain. The last cause names the interface or scheme
that failed to resolve; that name is the input to every probe below. A service
that restarts on failure needs `--previous`.

## 2. Establish the classpath the process actually uses

Never infer it from the install directory or the image documentation — a
persistent volume, an entrypoint script, or an operator-injected config can all
change it.

```bash
datus k8s --profile <profile> exec <pod> -n <namespace> -c <container> -- \
  sh -c 'ps -o pid=,comm= -A | grep -i java'
datus k8s --profile <profile> exec <pod> -n <namespace> -c <container> -- \
  sh -c "tr '\0' '\n' < /proc/<pid>/environ | sed -n 's/^CLASSPATH=//p' | tr ':' '\n'"
```

Read `/proc/<pid>/cmdline` the same way when the classpath is passed as `-cp`
rather than exported. Compare the result against the directories you assumed.

Also confirm the running build matches the deployed image. A tag says nothing
about what a persistent data directory contains:

```bash
datus k8s --profile <profile> get pod <pod> -n <namespace> -o yaml
```

Compare `spec.containers[].image` and `status.containerStatuses[].imageID` with
the version the service reports itself. A mismatch means the image is not what is
running, and every later conclusion is void until that is resolved.

## 3. Separate "JAR absent" from "SPI file absent"

```bash
datus k8s --profile <profile> exec <pod> -n <namespace> -c <container> -- \
  sh -c 'ls -1 <classpath-dir>/*.jar'
datus k8s --profile <profile> exec <pod> -n <namespace> -c <container> -- \
  sh -c 'jar tf <jar> | grep -E "<ExpectedClass>|META-INF/services/"'
```

A JAR that carries the class but no `META-INF/services/<interface>` entry can
never be found by `ServiceLoader`, no matter where it sits. Shaded and relocated
artifacts lose or rewrite these files, so check the JAR that is actually
deployed rather than the upstream one.

## 4. Separate "isolation" from "option validation"

Run the same lookup the service runs, from the same classpath, inside the same
pod. Two probes, and the pair of answers is decisive:

```bash
datus k8s --profile <profile> exec <pod> -n <namespace> -c <container> --timeout 3m -- \
  sh -c 'cd /tmp && cat > Probe.java <<'"'"'EOF'"'"'
import java.util.ServiceLoader;
public class Probe {
  public static void main(String[] a) throws Exception {
    Class<?> spi = Class.forName(a[0]);
    for (Object impl : ServiceLoader.load(spi, spi.getClassLoader())) {
      System.out.println("discovered " + impl.getClass().getName());
    }
    System.out.println("spi source " + spi.getProtectionDomain().getCodeSource());
  }
}
EOF
javac -cp "$CLASSPATH" Probe.java && java -cp "$CLASSPATH:/tmp" Probe <spi-interface>'
```

Quote the heredoc delimiter so the remote shell does not expand anything in the
Java source, and raise `--timeout` above the default request timeout: a cold
`javac` plus `java` easily exceeds 30 seconds and a truncated stream is reported
as a lost connection, not as a result.

- **Nothing discovered** — isolation or a genuinely absent SPI file. Check which
  loader the caller uses and where the JAR lives relative to it.
- **Implementation discovered, service still fails** — not discovery. The class
  loaded and then rejected its input: read its required options. This is where
  credentials, endpoints, and region settings that the outer product never
  translated into the library's own option names show up.

`getProtectionDomain().getCodeSource()` prints the JAR each class was really
loaded from; use it whenever two versions of the same library may be present.

Use `jshell` when the image ships it; fall back to `javac`/`java` as above; if
neither exists, run the probe in a sidecar or init container built from the same
base image rather than installing a JDK into a running pod.

## 5. Fix at the source, then re-verify

Apply the fix where it survives a restart — image contents, init container,
plugin directory, `flinkConfiguration`, catalog properties, a Secret — then
apply and re-check the resource with bounded, individual reads:

```bash
datus k8s --profile <profile> apply -f <manifest> -n <namespace> --dry-run server -o yaml
datus k8s --profile <profile> apply -f <manifest> -n <namespace>
datus k8s --profile <profile> get <kind>/<name> -n <namespace> -o wide
datus k8s --profile <profile> get <kind>/<name> -n <namespace> \
  -o 'jsonpath={.status.error}'
datus k8s --profile <profile> events -n <namespace> --for <kind>/<name>
```

Check both the field that reports the workload and the fields that report its
container or controller. A classpath fix that did not work often fails in
seconds; `status.error`, pods, or warning events may contain the answer before
the workload state changes.

Re-run the probe that failed. A fix is confirmed by the probe changing its
answer, not by the absence of the old log line.

## Rules

- Probes are read-only. Do not install packages, edit files, or restart processes
  inside a container: the next restart erases it and the manifest still lies.
- One question per probe. A compound command that fails tells you nothing about
  which part failed.
- Never conclude "the JAR is missing" from a `ServiceLoader` failure alone, and
  never conclude "it is a classloader problem" before reading the implementation's
  required options.
- Report the classpath, the JAR, the SPI entry, and the probe output that support
  the conclusion. Each of the four causes needs different evidence.
