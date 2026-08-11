---
name: flink-local-dev
description: Run and validate an Apache Flink SQL job on the current machine, in an in-process MiniCluster driven by the Flink SQL Client, before promoting it to production. Use for local Flink SQL development, debugging a SQL script, bounded replay of development Kafka/JDBC/CDC sources, shadowing every sink with print/blackhole/local filesystem, EXPLAIN plan checks, output assertions, and the parity checklist that precedes handing the job to `flink-k8s-operator`. A local run never writes to a production sink, consumer group, or checkpoint path.
---

# Flink local development

Validate Flink SQL logic where the code lives: one JVM, one MiniCluster, no
Kubernetes, no Docker, no shared cluster. The job script that passes here is the
same file that ships to production; everything local lives in a separate overlay.

Copy the templates below into the project, replace every `__PLACEHOLDER__`, and
delete the variants you do not use. They are starting points, not defaults.

## 1. Establish the target

Collect or derive these before changing files:

- the SQL script under validation, and which statements are the job (a single
  `INSERT INTO` or an `EXECUTE STATEMENT SET`)
- the production Flink version this job will run on (`spec.flinkVersion` such as
  `v1_20` -> Flink `1.20.x`)
- every source table and, for each, whether the local run uses a development
  endpoint or a generated stand-in
- every sink table — each one needs a local shadow
- the connector jars the script needs, and their versions
- expected output: row count, sample rows, changelog kinds, or an invariant the
  result must satisfy

Use this layout, next to the Operator manifests the `flink-k8s-operator` skill
generates:

```
deploy/flink/<name>/
├── sql/job.sql                 # the production artifact — identical locally
└── local/
    ├── local-session.sql       # local session settings
    ├── local-sources.sql       # bounded/dev source shadows (optional)
    ├── local-sinks.sql         # sink shadows (required when the job writes)
    └── run-local-sql.sh        # preflight + guards + SQL Client invocation
```

Inspect existing files first and never overwrite them silently. Ask which
statements form the job when the script mixes DDL, DML, and ad-hoc queries.

## 2. Preflight the local runtime (fail closed)

A local run needs a Flink distribution on this machine — `apache-flink` in a
virtualenv is not one:

```bash
echo "${FLINK_HOME:?set FLINK_HOME to a local Flink distribution}"
"$FLINK_HOME/bin/flink" --version     # -> Version: 1.20.1, Commit ID: ...
java -version
```

Two invariants:

- The distribution's minor version matches the production `flinkVersion`. A
  different minor version changes planner behaviour and connector
  compatibility, which defeats the point of validating locally.
- The JDK is one that release supports. Flink 1.x releases target Java 8/11/17
  and Flink 2.x dropped Java 8; check the release's own compatibility note
  rather than assuming. An unsupported JDK fails with
  `InaccessibleObjectException` or a module-access error, not a version
  message — point `JAVA_HOME` at a supported JDK instead of adding
  `--add-opens` flags.

Then prove the in-process path works, before any real endpoint is involved:

```bash
"$FLINK_HOME/bin/sql-client.sh" -f local/minicluster-smoke.sql
```

### minicluster-smoke.sql

```sql
-- Proves the in-process MiniCluster path works. Expect five +I[...] rows on
-- stdout and exit code 0. Reads nothing, writes nothing, needs no connector jar.

SET 'execution.target' = 'local';
SET 'table.dml-sync' = 'true';
SET 'parallelism.default' = '1';
SET 'sql-client.execution.result-mode' = 'tableau';

CREATE TEMPORARY TABLE local_smoke_source (
  id BIGINT,
  label STRING
) WITH (
  -- A sequence field bounds the table: it ends when the sequence completes.
  'connector' = 'datagen',
  'fields.id.kind' = 'sequence',
  'fields.id.start' = '1',
  'fields.id.end' = '5',
  'fields.label.length' = '4'
);

CREATE TEMPORARY TABLE local_smoke_sink (
  id BIGINT,
  label STRING
) WITH (
  'connector' = 'print',
  'print-identifier' = 'local_smoke_sink'
);

INSERT INTO local_smoke_sink SELECT id, label FROM local_smoke_source;
```

`SET 'execution.target' = 'local'` selects Flink's `LocalExecutor`, which builds
the JobGraph and submits it to a MiniCluster inside the client JVM: no
JobManager or TaskManager daemon, nothing surviving the process, no
`flink-conf.yaml` cluster address. The SQL Client documentation only describes
the `start-cluster.sh` + remote-target workflow, so treat the local target as
verified-by-probe — run the smoke script once per distribution before trusting
it.

Stop with an actionable message when `FLINK_HOME` is unset, the version does not
match production, the JDK is unsupported, a connector factory is missing, or the
smoke run fails. If a release rejects the local target, **do not** let the run
fall through to the default target — with a real development source configured,
that submits the job to whatever cluster `flink-conf.yaml` points at. Fix the
version, or ask the user before starting a local standalone cluster
(`"$FLINK_HOME/bin/start-cluster.sh"`, remote target, UI on `localhost:8081`) and
stop it afterwards.

## 3. Bound the blast radius

A local MiniCluster is isolated in compute only — its connectors reach exactly as
far as the credentials you give them. Non-negotiable:

1. Sources may be real development endpoints; sinks may not.
2. Read-only credentials, and an identity of its own — never a production
   consumer group, CDC server id, replication slot, or service account.
3. Bounded reads by default, so a validation run cannot sit on a shared cluster
   for hours.
4. Local state and output stay under a local scratch directory.
5. Credentials live only in a git-ignored file.

Ask the user to name each endpoint and confirm it is development or staging.
Never infer it from the hostname, and never reuse an endpoint found in a
production manifest. When the only available endpoint is production, stop and say
so: generate the data instead (`datagen`, a filesystem fixture, or `VALUES`).
Reuse the endpoints and accounts the project already has; do not invent
hostnames or create credentials.

**Kafka.** Set a dedicated `properties.group.id` (`local-dev-<user>-<job>`) — when
the option is omitted the connector generates `KafkaSource-<tableIdentifier>`,
which collides across runs. Leave checkpointing off: offsets are committed back
to Kafka on checkpoint, and that is the one way a read-only source run can still
change shared state. `earliest-offset` on a large topic reads the whole retention
window; prefer a timestamp slice. Never declare a Kafka sink locally.

**JDBC.** Use an account with `SELECT` only, and verify it — an account that can
write is a failed guardrail even if this run happens not to write. Keep the scan
small (a `LIMIT`ed query, a partitioned scan, a narrow predicate); a full-table
scan on a shared development database is a visible load event. Lookup joins issue
per-row queries: set a cache or bound the driving side first.

**CDC.** The highest-risk local source, because it registers as a replication
client on a shared database. MySQL `server-id` must be unique across every
client, and a parallel incremental snapshot needs a range
(`'server-id' = '<start>-<end>'`) wide enough for the reader count — a collision
with production breaks the production job. Postgres `slot.name` must be unique,
and a leftover slot retains WAL indefinitely and can fill the primary's disk, so
drop it after the run. Prefer `scan.startup.mode = 'latest-offset'`: the default
snapshot reads whole tables. A CDC source never ends — rely on the timeout and
treat the run as a sampled observation.

**Sinks.** Every `INSERT INTO`/`INSERT OVERWRITE` target gets a
`CREATE TEMPORARY TABLE` shadow whose column list matches the production DDL
exactly. Allowed local connectors:

| Connector | Use it for | Note |
|---|---|---|
| `print` | inspecting rows and changelog kinds | rows land on client stdout |
| `blackhole` | plan, throughput, and state checks | produces no output |
| `filesystem` on a `file://` path | keeping output to diff between runs | commits part files only on checkpoint |

A temporary table shadows the permanent or script-declared table of the same
name and makes it inaccessible for the session, so the job script needs no edit.
If the user insists on a real sink, that is no longer a local validation run:
require explicit approval, a dedicated scratch topic/table (`local_dev_` prefix),
and non-production credentials — then say plainly in the report that a real
system was written to.

**State paths.** Local checkpoint, savepoint, and output directories live under a
scratch path such as `/tmp/datus-flink-local/<name>/`. Never point them at
production state: a local job writing there can corrupt what a production job
depends on. Validating against production state is copy-then-restore — copy the
savepoint locally, restore from the copy.

**Credentials.** Flink SQL DDL does not expand environment variables, so a
development password in a `WITH` clause is a literal. Keep every overlay file
that carries one out of version control (`deploy/flink/*/local/*.local.sql` in
`.gitignore`); never write a credential into `sql/job.sql` — production reads
secrets from Kubernetes Secrets; never echo an overlay file into the transcript,
a log, or a report.

Stop and ask instead of guessing when an endpoint's environment is unconfirmed,
a sink has no local shadow, the credential offered can write, a CDC
`server-id`/`slot.name` cannot be confirmed free, an unbounded source would run
without a timeout, or a checkpoint path is not local.

## 4. Assemble the local overlay

Three files, loaded in this order through the SQL Client's `-i` option. `-i`
takes a single file, so the runner concatenates them; build the same file by hand
(`cat local/local-session.sql local/local-sources.sql local/local-sinks.sql >
/tmp/local-init.sql`) when invoking the client directly. An init script accepts
`SET`, `RESET`, DDL, `USE`, and `LOAD/UNLOAD MODULE` — not queries or inserts.

Leave `sql/job.sql` untouched. If a local run only works after editing the job
script, that edit is either a real fix (keep it, it belongs in production) or a
local workaround (move it to the overlay).

### local-session.sql

```sql
-- Local validation session settings for __JOB_NAME__.
-- Local only: never copy these into sql/job.sql or spec.flinkConfiguration.

-- Required. Runs the job in a MiniCluster inside this client JVM instead of
-- submitting it to whatever cluster flink-conf.yaml points at.
SET 'execution.target' = 'local';

-- Required. Waits for the INSERT job to finish so the script's exit code
-- reflects the job result instead of just the submission.
SET 'table.dml-sync' = 'true';

-- Must match production.
SET 'execution.runtime-mode' = '__RUNTIME_MODE__';
SET 'table.local-time-zone' = '__TIME_ZONE__';

-- Start at 1 to keep output readable; re-run at the production value once the
-- logic is right, to expose ordering and keyed-state assumptions.
SET 'parallelism.default' = '__PARALLELISM__';

SET 'sql-client.execution.result-mode' = 'tableau';

-- Off while validating logic: checkpointing adds latency and is what would
-- commit Kafka offsets back to a shared consumer group. Enable it only to test
-- a filesystem sink, state size, or restore behaviour — always to a local path.
-- SET 'execution.checkpointing.interval' = '10s';
-- SET 'state.checkpoints.dir' = 'file://__LOCAL_STATE_DIR__/checkpoints';
-- Flink 2.x renamed the option above to execution.checkpointing.dir.

-- Uncomment when a windowed or interval query emits nothing because one
-- partition is idle and the watermark never advances.
-- SET 'table.exec.source.idle-timeout' = '10s';
```

### local-sources.sql

```sql
-- Optional local source overlay for __JOB_NAME__. The column list, types, and
-- watermark must match the production DDL exactly. Read development endpoints
-- only, read-only, bounded. Keep this file git-ignored when it carries a
-- credential.

-- Bounded slice of a development Kafka topic.
CREATE TEMPORARY TABLE __SOURCE_TABLE__ (
  __SOURCE_COLUMNS__
) WITH (
  'connector' = 'kafka',
  'topic' = '__DEV_TOPIC__',
  'properties.bootstrap.servers' = '__DEV_BOOTSTRAP_SERVERS__',
  -- Dedicated group: never a production consumer group.
  'properties.group.id' = 'local-dev-__LOCAL_RUN_ID__',
  -- Recent slice, so the run reads real data and still terminates.
  'scan.startup.mode' = 'timestamp',
  'scan.startup.timestamp-millis' = '__START_TIMESTAMP_MILLIS__',
  'scan.bounded.mode' = 'latest-offset',
  'format' = 'json',
  'json.ignore-parse-errors' = 'false'
);

-- Variant: generated data, when no development endpoint may be read.
-- CREATE TEMPORARY TABLE __SOURCE_TABLE__ (
--   __SOURCE_COLUMNS__
-- ) WITH (
--   'connector' = 'datagen',
--   -- A sequence field bounds the table; 'number-of-rows' bounds a random one.
--   'fields.__SOURCE_KEY_COLUMN__.kind' = 'sequence',
--   'fields.__SOURCE_KEY_COLUMN__.start' = '1',
--   'fields.__SOURCE_KEY_COLUMN__.end' = '20'
-- );

-- Variant: fixture files checked in next to the job, fully reproducible.
-- CREATE TEMPORARY TABLE __SOURCE_TABLE__ (
--   __SOURCE_COLUMNS__
-- ) WITH (
--   'connector' = 'filesystem',
--   'path' = 'file://__LOCAL_FIXTURE_DIR__/__SOURCE_TABLE__',
--   'format' = 'json'
-- );

-- Variant: read-only development database. Keep the scan small, use an account
-- that cannot write, and .gitignore this file before adding a password.
-- CREATE TEMPORARY TABLE __SOURCE_TABLE__ (
--   __SOURCE_COLUMNS__
-- ) WITH (
--   'connector' = 'jdbc',
--   'url' = 'jdbc:mysql://__DEV_DB_HOST__:3306/__DEV_DB__',
--   'table-name' = '__DEV_TABLE__',
--   'username' = '__DEV_READONLY_USER__',
--   'password' = '__DEV_READONLY_PASSWORD__'
-- );
```

### local-sinks.sql

```sql
-- Required local sink overlay for __JOB_NAME__. Every INSERT INTO /
-- INSERT OVERWRITE target in the job script needs a shadow here, with the
-- production column list. Only print, blackhole, and filesystem on a file://
-- path are allowed: a local run never writes to a real topic, table, or object
-- store.

CREATE TEMPORARY TABLE __SINK_TABLE__ (
  __SINK_COLUMNS__
) WITH (
  'connector' = 'print',
  'print-identifier' = '__SINK_TABLE__'
);

-- Variant: keep the output as files to diff between runs. The filesystem sink
-- commits part files only on checkpoint, so enable
-- execution.checkpointing.interval in local-session.sql when using this.
-- CREATE TEMPORARY TABLE __SINK_TABLE__ (
--   __SINK_COLUMNS__
-- ) WITH (
--   'connector' = 'filesystem',
--   'path' = 'file://__LOCAL_OUTPUT_DIR__/__SINK_TABLE__',
--   'format' = 'json'
-- );

-- Variant: discard the rows, for plan, state, and throughput checks only.
-- CREATE TEMPORARY TABLE __SINK_TABLE__ (
--   __SINK_COLUMNS__
-- ) WITH (
--   'connector' = 'blackhole'
-- );
```

## 5. Run it through the guarded runner

Write this script into `local/run-local-sql.sh` and run the job through it. It
fails closed: no unpinned session, no unshadowed sink, no non-local sink, no
tracked credential. Never bypass a guard by calling `sql-client.sh` directly
with a real endpoint configured.

### run-local-sql.sh

```bash
#!/usr/bin/env bash
#
# Run a Flink SQL job in an in-process MiniCluster for local validation.
# Exit codes: 0 job ok · 2 usage · 3 environment · 4 guard · 5 timeout ·
# anything else is the SQL Client's own exit code.
set -euo pipefail

JOBS=(); JARS=(); SESSION=""; SOURCES=""; SINKS=""; TIMEOUT_SECONDS=600; EXPECT=""
USAGE="usage: run-local-sql.sh --job <file.sql>... --session <local-session.sql>
       [--sinks <local-sinks.sql>] [--sources <local-sources.sql>] [--jar <connector.jar>]...
       [--timeout <seconds>] [--expect-flink-version <major.minor>]"

log() { printf 'run-local-sql: %s\n' "$1" >&2; }
die() { printf 'run-local-sql: %s\n' "$1" >&2; exit "${2:-1}"; }
# SQL with `--` line comments removed, so a commented-out example never
# satisfies — or trips — a guard.
active_sql() { sed -e 's/--.*$//' "$@"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --job|--jar|--session|--sources|--sinks|--timeout|--expect-flink-version)
      [ $# -ge 2 ] || die "missing value for $1
$USAGE" 2 ;;
    *) die "unknown argument: $1
$USAGE" 2 ;;
  esac
  case "$1" in
    --job)     JOBS+=("$2") ;;
    --jar)     JARS+=("$2") ;;
    --session) SESSION="$2" ;;
    --sources) SOURCES="$2" ;;
    --sinks)   SINKS="$2" ;;
    --timeout) TIMEOUT_SECONDS="$2" ;;
    *)         EXPECT="$2" ;;
  esac
  shift 2
done

[ ${#JOBS[@]} -gt 0 ] && [ -n "$SESSION" ] || die "$USAGE" 2
for f in "${JOBS[@]}" "$SESSION" ${SOURCES:+"$SOURCES"} ${SINKS:+"$SINKS"}; do
  [ -f "$f" ] || die "file not found: $f" 2
done
case "$TIMEOUT_SECONDS" in ''|*[!0-9]*) die "--timeout takes whole seconds, got: $TIMEOUT_SECONDS" 2 ;; esac

# --- environment ---
[ -n "${FLINK_HOME:-}" ] || die "FLINK_HOME is not set; point it at a local Flink distribution" 3
SQL_CLIENT="$FLINK_HOME/bin/sql-client.sh"
[ -x "$SQL_CLIENT" ] || die "not executable: $SQL_CLIENT" 3

FLINK_VERSION=""
[ -x "$FLINK_HOME/bin/flink" ] && FLINK_VERSION="$( ("$FLINK_HOME/bin/flink" --version 2>/dev/null || true) |
  sed -n 's/.*[Vv]ersion: *\([0-9][0-9.]*\).*/\1/p' | head -1)"
if [ -n "$EXPECT" ]; then
  [ -n "$FLINK_VERSION" ] || die "cannot read the distribution version; expected $EXPECT" 3
  case "$FLINK_VERSION" in
    "$EXPECT"|"$EXPECT".*) : ;;
    *) die "Flink $FLINK_VERSION does not match production $EXPECT; validating on a different minor version proves little" 3 ;;
  esac
fi
JAVA_MAJOR="$( (java -version 2>&1 || true) | sed -n '1s/.*version "\([0-9][0-9]*\).*/\1/p')"
case "$FLINK_VERSION:$JAVA_MAJOR" in
  1.*:1[89]|1.*:2[0-9]) log "warning: Flink $FLINK_VERSION on Java $JAVA_MAJOR is likely unsupported; expect module-access errors" ;;
esac

# --- guards ---
active_sql "$SESSION" | grep -Eqi "'execution\.target'[[:space:]]*=[[:space:]]*'local'" ||
  die "$SESSION must pin: SET 'execution.target' = 'local'; — otherwise the job goes to whatever flink-conf.yaml points at" 4
active_sql "$SESSION" | grep -Eqi "'table\.dml-sync'[[:space:]]*=[[:space:]]*'true'" ||
  die "$SESSION must pin: SET 'table.dml-sync' = 'true'; — otherwise the script exits before the job runs" 4

# A CTAS writes through a connector the overlay cannot shadow.
if active_sql "${JOBS[@]}" | tr '\n' ' ' |
  grep -Eqi "(create|replace)[[:space:]]+(or[[:space:]]+replace[[:space:]]+)?table[[:space:]][^;]*[[:space:]]as[[:space:]]+select"; then
  die "the job uses CREATE/REPLACE TABLE AS SELECT, which a temporary shadow cannot intercept — split the DDL from the INSERT before validating locally" 4
fi

bare_names() { awk '{print $NF}' | tr -d '`"' | awk -F. '{print $NF}' | sort -u; }
TARGETS="$(active_sql "${JOBS[@]}" | tr '\n' ' ' |
  grep -Eio "insert[[:space:]]+(into|overwrite)[[:space:]]+[A-Za-z0-9_.\`\"]+" | bare_names || true)"
SHADOWS=""
[ -z "$SINKS" ] || SHADOWS="$(active_sql "$SINKS" | tr '\n' ' ' |
  grep -Eio "create[[:space:]]+temporary[[:space:]]+table[[:space:]]+(if[[:space:]]+not[[:space:]]+exists[[:space:]]+)?[A-Za-z0-9_.\`\"]+" | bare_names || true)"

if [ -n "$TARGETS" ]; then
  [ -n "$SINKS" ] || die "the job writes to $(echo "$TARGETS" | tr '\n' ' ')— pass --sinks with a temporary shadow for each" 4
  MISSING=""
  for target in $TARGETS; do
    echo "$SHADOWS" | grep -Fqx "$target" || MISSING="$MISSING $target"
  done
  [ -z "$MISSING" ] || die "no local sink shadow for:$MISSING — add a CREATE TEMPORARY TABLE to $SINKS" 4
fi

if [ -n "$SINKS" ]; then
  UNSAFE="$(active_sql "$SINKS" | grep -Eio "'connector'[[:space:]]*=[[:space:]]*'[A-Za-z0-9_-]+'" |
    sed -E "s/.*'([A-Za-z0-9_-]+)'\$/\1/" | sort -u | grep -Eiv '^(print|blackhole|filesystem)$' || true)"
  [ -z "$UNSAFE" ] || die "$SINKS uses non-local sink connector(s) $(echo "$UNSAFE" | tr '\n' ' ')— only print, blackhole, and filesystem(file://) are allowed locally" 4
  REMOTE_PATH="$(active_sql "$SINKS" | grep -Eio "'path'[[:space:]]*=[[:space:]]*'[^']*'" |
    sed -E "s/.*'([^']*)'\$/\1/" | grep -Ei '^[a-z][a-z0-9+.-]*://' | grep -Eiv '^file://' || true)"
  [ -z "$REMOTE_PATH" ] || die "$SINKS writes to a non-local path $(echo "$REMOTE_PATH" | tr '\n' ' ')— use file:// under a local scratch directory" 4
fi

# A development credential in a WITH clause is a literal (SQL DDL does not
# expand environment variables), so the file must not be tracked.
credential_guard() {
  file="$1"
  [ -n "$file" ] || return 0
  active_sql "$file" |
    grep -Eqi "'[A-Za-z0-9._-]*(password|secret|token|access[-_.]?key)[A-Za-z0-9._-]*'[[:space:]]*=" || return 0
  dir="$(cd "$(dirname "$file")" && pwd)"
  git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  git -C "$dir" check-ignore -q "$dir/$(basename "$file")" && return 0
  die "$file holds a credential and is not git-ignored; add it to .gitignore before running" 4
}
credential_guard "$SESSION"
credential_guard "$SOURCES"
credential_guard "$SINKS"

# --- run ---
INIT="$(mktemp "${TMPDIR:-/tmp}/flink-local-init.XXXXXX")"
TEMP_SCRIPT=""
trap 'rm -f "$INIT" "$TEMP_SCRIPT"' EXIT
cat "$SESSION" ${SOURCES:+"$SOURCES"} ${SINKS:+"$SINKS"} > "$INIT"
# Keep the original path when there is only one job file, so the client's error
# messages point at a file the user can edit.
if [ ${#JOBS[@]} -eq 1 ]; then
  SCRIPT="${JOBS[0]}"
else
  TEMP_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/flink-local-job.XXXXXX")"
  cat "${JOBS[@]}" > "$TEMP_SCRIPT"
  SCRIPT="$TEMP_SCRIPT"
fi

CMD=("$SQL_CLIENT" -i "$INIT")
for jar in ${JARS[@]+"${JARS[@]}"}; do
  [ -f "$jar" ] || die "connector jar not found: $jar" 3
  CMD+=(-j "$jar")
done
CMD+=(-f "$SCRIPT")

TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT_BIN="gtimeout"
else log "warning: no timeout/gtimeout on PATH; the run is uncapped — stop it with Ctrl-C"
fi

log "Flink ${FLINK_VERSION:-unknown} · target=local · dml-sync=true"
[ -z "$TARGETS" ] || log "sink shadows verified: $(echo "$TARGETS" | tr '\n' ' ')"
log "job=$(echo "${JOBS[@]}" | tr ' ' ',')"

set +e
if [ -n "$TIMEOUT_BIN" ]; then "$TIMEOUT_BIN" "$TIMEOUT_SECONDS" "${CMD[@]}"; else "${CMD[@]}"; fi
rc=$?
set -e

if [ -n "$TIMEOUT_BIN" ] && [ "$rc" -eq 124 ]; then
  die "the job did not finish within ${TIMEOUT_SECONDS}s — bound every source (scan.bounded.mode / number-of-rows) or treat the run as sampled" 5
fi
[ "$rc" -eq 0 ] || die "the SQL Client exited with $rc — read the error above before changing anything" "$rc"
log "job finished; now check the rows, changelog kinds, and counts against what you expected"
```

Invoke it, passing `--job` once per file in execution order:

```bash
local/run-local-sql.sh \
  --job sql/job.sql \
  --session local/local-session.sql \
  --sources local/local-sources.sql \
  --sinks local/local-sinks.sql \
  --jar <connector-dir>/flink-sql-connector-kafka-<version>.jar \
  --expect-flink-version <major.minor> \
  --timeout 300
```

Connector jars come from `$FLINK_HOME/lib` plus whatever `-j`/`-l` adds. Use the
SQL uber jars built for the exact Flink minor version, with the matching Scala
suffix where one exists, and record which jars the run used: the production image
must contain the same set at the same versions.

## 6. Make the run terminate

An in-process run that never finishes holds a MiniCluster and a real source
connection until it is killed. Three layers:

1. `table.dml-sync = 'true'` — the default (`false`) submits the INSERT
   asynchronously, so the script exits, the JVM dies, and the MiniCluster tears
   the job down mid-flight.
2. Bounded sources — Kafka `scan.bounded.mode` (`latest-offset`, `group-offsets`,
   `timestamp`, `specific-offsets`) with a `scan.startup.mode` that starts far
   enough back to contain data; `datagen` `number-of-rows` or a `sequence` field;
   JDBC and filesystem scans are bounded already; CDC never ends.
3. `--timeout <seconds>` — wraps the client in `timeout`, falling back to
   `gtimeout`. macOS ships neither by default: install coreutils or accept an
   uncapped run.

## 7. Judge the result

Judge on evidence, not on the absence of a stack trace:

- exit code 0 and, with `table.dml-sync`, a job that reached `FINISHED`
- the produced rows: count, sample values, and changelog kinds against what the
  query is supposed to emit. A `print` sink writes `+I[value, ...]` to stdout,
  prefixed by `print-identifier`; `-U`/`+U` pairs and `-D` mean the query is an
  updating one — if production writes to an append-only sink, that is a defect
  this run just caught
- no output at all is a failure to investigate — an empty bounded slice, a
  watermark that never advanced, or a filter matching nothing
- warnings that change results: parse errors ignored, not-null enforcement,
  upsert materialization, late records dropped

Report the actual observed output. Do not describe a run that did not happen.

`EXPLAIN` compiles without executing — use it to see *how* the job will run, or
when a source is expensive to read. The planner compiles the whole script before
submission anyway, so syntax, schema, type, and connector-option errors surface
without reading a row:

```sql
EXPLAIN INSERT INTO orders_sink SELECT ... FROM orders_source;

EXPLAIN STATEMENT SET
BEGIN
  INSERT INTO sink_a SELECT ...;
  INSERT INTO sink_b SELECT ...;
END;
```

Run it with the same `-i` overlay and, when the DDL lives in the job script, with
the DDL ahead of it in the same file — keeping `sql/ddl.sql` and `sql/dml.sql`
separate makes this reuse one canonical text. Read the plan for changelog mode
per node, join type (regular join with unbounded state versus
interval/temporal/lookup), state TTL, aggregate splitting, and the sink node's
table identifier — which is how you confirm the shadow, not the production table,
is what the job would write to.

| Symptom | Likely cause | Action |
|---|---|---|
| `Could not find any factory for identifier 'kafka'` | connector jar missing or wrong Flink version | add the right SQL uber jar with `-j` |
| `Unknown execution.target 'local'` / no executor factory | release does not accept the local target | fix the version, or ask before starting a local standalone cluster |
| `InaccessibleObjectException`, module access errors | JDK newer than the release supports | point `JAVA_HOME` at a supported JDK |
| Script exits immediately, job never ran | `table.dml-sync` left at `false` | pin it to `true` |
| Run hangs until the timeout | unbounded source | set `scan.bounded.mode`/`number-of-rows`, or accept a sampled run |
| No rows at all | startup offset past the data, or a filter matching nothing | widen the startup offset; check with `SELECT * FROM <source> LIMIT 10` |
| Windowed/interval query emits nothing | watermark never advanced (idle partition) | set `table.exec.source.idle-timeout`; check the event-time column |
| Timestamps differ from production | session time zone is the machine's | pin `table.local-time-zone` |
| `NoSuchMethodError`, `ClassNotFoundException` | jar/Scala/Flink version mismatch, or two connector versions on the classpath | keep one version per connector |
| Filesystem sink directory looks empty | no checkpointing, so part files stay in progress | enable a short checkpoint interval to a local directory |

## 8. Iterate without breaking parity

Re-run after every change; keep each iteration bounded and small. Raise
`parallelism.default` above 1 once the logic is right, to expose ordering and
keyed-state assumptions. Enable checkpointing locally only to test state, a
filesystem sink, or restore behaviour — always to a local scratch directory.

Keep `sql/job.sql` the single canonical artifact. Before promotion:

```bash
git diff -- deploy/flink/<name>/sql/
git status --short deploy/flink/<name>/local/
```

The first must show only intentional logic changes — no `print` connector, no
development endpoint, no credential. The second must show nothing tracked that
carries a credential.

## 9. Promote to production

| Verified locally | Not verified locally |
|---|---|
| SQL parses, types and schemas resolve, options are accepted | real sink behaviour, credentials, and permissions |
| the plan Flink builds for this query | cluster-side plan under production parallelism |
| result values and changelog kinds on a bounded slice | correctness over the full production stream |
| source connectivity and format decoding for the dev endpoint | production endpoint reachability and authorization |
| that the job finishes (bounded) or runs (sampled) | long-run stability, backpressure, state growth |
| behaviour at the parallelism you ran | key skew, ordering across subtasks, watermark alignment |
| — | exactly-once delivery, transactional sinks, HA/failover |
| — | checkpoint duration, savepoint size, restore compatibility |
| — | JobManager/TaskManager sizing, quotas, image pull, RBAC |

State the second column explicitly in the report: a passing local run is evidence
about logic, not a production readiness certificate.

Confirm each parity item matches, or is a deliberate, stated difference: Flink
version; connector jars and versions present in the production image; SQL text
byte-identical except connector `WITH` options; runtime mode; `table.local-time-zone`;
parallelism-sensitive logic (`ORDER BY` without a time attribute, `LIMIT`,
non-deterministic functions, per-key assumptions); planner options that change
results (mini-batch, distinct-aggregate split,
`table.exec.sink.not-null-enforcer`, `table.exec.sink.upsert-materialize`, state
TTL, idle-source timeout); catalog/database/table identifiers, column order,
nullability, and format options; watermark expression and allowed lateness.

Session settings from `local-session.sql` do not travel with the script. Their
production equivalents belong in `spec.flinkConfiguration` (time zone, planner
options, checkpoint interval and directory, parallelism);
`execution.target` and `table.dml-sync` are local-only — never copy them into a
manifest.

The Flink Kubernetes Operator submits **jars**, not SQL scripts, so a validated
script needs a runner. Decide with the user which one and record it next to the
manifest:

- **A SQL runner jar** — a small application that reads the script from the image
  or a mounted volume and executes its statements in order (the Operator project
  ships a `flink-sql-runner-example` illustrating the pattern). The script becomes
  an image layer or a ConfigMap/volume; the manifest passes its path as a job
  argument.
- **A SQL Gateway against a session cluster** — the script is submitted to a
  long-lived cluster instead of being packaged. This changes the operational
  model; raise it explicitly rather than adopting it by default.

Either way the deployed script must be the same text that passed locally — verify
it after packaging by reading the file back out of the image or volume.

Then invoke the `flink-k8s-operator` skill with the values this validation
settled: Flink version and base image, the runner decision and the script's
location, connector jars with versions, production parallelism and task slots,
the real endpoints and the Kubernetes Secrets holding their credentials,
checkpoint/savepoint storage and upgrade mode, and the expected output to compare
against the first production rows. That skill runs its own fail-closed preflight
and owns image delivery and the CR; do not pre-empt it, and do not build
production images from this skill.

After deployment, compare against the local expectation: job state and
reconciliation status, the first checkpoint completing, rows arriving in the real
sink with the same shape the local `print` sink showed, and no dropped-record or
parse-error warnings the local slice never triggered.

Clean up when finished: local scratch state and output directories, and any
temporary consumer group, CDC slot, or scratch table the run created. Keep the
overlay working — it is the reproduction harness for the next change.
