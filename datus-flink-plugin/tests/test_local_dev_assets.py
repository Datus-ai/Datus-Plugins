"""Validate the local-development overlay templates and the runner's fail-closed guards.

Everything under test lives inline in the single `flink-local-dev/SKILL.md`; the
tests extract each `### <filename>` block and exercise it exactly as a project
would after copying it out.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_blocks import blocks, render

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "datus_flink_plugin" / "skills" / "flink-local-dev" / "SKILL.md"

SAFE_SINK_CONNECTORS = {"print", "blackhole", "filesystem"}
OVERLAY_FILES = ("local-session.sql", "local-sources.sql", "local-sinks.sql")

VALUES = {
    "__JOB_NAME__": "orders",
    "__RUNTIME_MODE__": "streaming",
    "__PARALLELISM__": "1",
    "__TIME_ZONE__": "UTC",
    "__LOCAL_STATE_DIR__": "/tmp/datus-flink-local/orders/state",
    "__LOCAL_OUTPUT_DIR__": "/tmp/datus-flink-local/orders/out",
    "__LOCAL_FIXTURE_DIR__": "/tmp/datus-flink-local/orders/fixtures",
    "__SOURCE_TABLE__": "orders_source",
    "__SOURCE_COLUMNS__": "order_id BIGINT,\n  amount DECIMAL(10, 2)",
    "__SOURCE_KEY_COLUMN__": "order_id",
    "__DEV_TOPIC__": "orders-dev",
    "__DEV_BOOTSTRAP_SERVERS__": "kafka-dev.internal:9092",
    "__LOCAL_RUN_ID__": "felix-orders",
    "__START_TIMESTAMP_MILLIS__": "1754265600000",
    "__SINK_TABLE__": "orders_sink",
    "__SINK_COLUMNS__": "order_id BIGINT,\n  amount DECIMAL(10, 2)",
    "__DEV_DB_HOST__": "db-dev.internal",
    "__DEV_DB__": "shop",
    "__DEV_TABLE__": "orders",
    "__DEV_READONLY_USER__": "orders_ro",
    "__DEV_READONLY_PASSWORD__": "not-a-real-password",
}


def template(name: str) -> str:
    return render(blocks(SKILL)[name], VALUES)


def runner_source() -> str:
    return blocks(SKILL)["run-local-sql.sh"]


def active(sql: str) -> str:
    """The statements the SQL Client would run, with `--` comments removed."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def connectors(sql: str) -> set[str]:
    return set(re.findall(r"'connector'\s*=\s*'([A-Za-z0-9_-]+)'", sql))


def test_skill_file_carries_every_overlay_and_the_runner_inline():
    assert set(blocks(SKILL)) == {
        "minicluster-smoke.sql",
        *OVERLAY_FILES,
        "run-local-sql.sh",
    }


def test_session_overlay_pins_in_process_execution_and_synchronous_dml():
    sql = active(template("local-session.sql"))
    assert re.search(r"SET 'execution\.target' = 'local';", sql)
    assert re.search(r"SET 'table\.dml-sync' = 'true';", sql)
    assert "remote" not in sql


def test_session_overlay_pins_the_settings_that_change_results():
    sql = active(template("local-session.sql"))
    for option in ("execution.runtime-mode", "table.local-time-zone", "parallelism.default"):
        assert f"SET '{option}'" in sql, option


def test_session_overlay_leaves_checkpointing_off_and_state_local():
    rendered = template("local-session.sql")
    assert "execution.checkpointing.interval" not in active(rendered)
    assert "execution.checkpointing.interval" in rendered
    for path in re.findall(r"'state\.checkpoints\.dir' = '([^']+)'", rendered):
        assert path.startswith("file:///"), path


def test_source_overlay_shadows_with_a_bounded_read_only_dev_slice():
    sql = active(template("local-sources.sql"))
    assert "CREATE TEMPORARY TABLE orders_source" in sql
    assert "CREATE TABLE" not in sql.replace("CREATE TEMPORARY TABLE", "")
    assert "'scan.bounded.mode' = 'latest-offset'" in sql
    group_ids = re.findall(r"'properties\.group\.id' = '([^']+)'", sql)
    assert group_ids and all(g.startswith("local-dev-") for g in group_ids), group_ids
    assert connectors(sql) & SAFE_SINK_CONNECTORS == set()


def test_sink_overlay_only_declares_local_sinks():
    rendered = template("local-sinks.sql")
    sql = active(rendered)
    assert "CREATE TEMPORARY TABLE orders_sink" in sql
    assert "CREATE TABLE" not in sql.replace("CREATE TEMPORARY TABLE", "")
    assert connectors(sql) <= SAFE_SINK_CONNECTORS, connectors(sql)
    for path in re.findall(r"'path' = '([^']+)'", rendered):
        assert path.startswith("file://"), path


def test_smoke_script_reads_nothing_and_terminates():
    sql = active(template("minicluster-smoke.sql"))
    assert connectors(sql) == {"datagen", "print"}
    assert "'fields.id.kind' = 'sequence'" in sql
    assert "SET 'execution.target' = 'local';" in sql
    assert "SET 'table.dml-sync' = 'true';" in sql


def test_runner_is_strict_bash_and_starts_no_cluster_or_container(tmp_path: Path):
    text = runner_source()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    for forbidden in ("kubectl", "docker", "minikube", "start-cluster.sh"):
        assert forbidden not in text, forbidden
    script = tmp_path / "run-local-sql.sh"
    script.write_text(text, encoding="utf-8")
    subprocess.run(["bash", "-n", str(script)], check=True)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A project with a recording fake Flink distribution and the extracted runner."""
    bin_dir = tmp_path / "flink" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "sql-client.sh").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$ARGV_LOG"\n'
        "while [ $# -gt 0 ]; do\n"
        '  [ "$1" = "-i" ] && cp "$2" "$INIT_COPY"\n'
        "  shift\n"
        "done\n"
        'exit "${FAKE_RC:-0}"\n',
        encoding="utf-8",
    )
    (bin_dir / "flink").write_text(
        '#!/usr/bin/env bash\necho "Version: ${FAKE_FLINK_VERSION:-1.20.1}, Commit ID: abc"\n',
        encoding="utf-8",
    )
    for script in bin_dir.iterdir():
        script.chmod(0o755)

    project = tmp_path / "project"
    (project / "sql").mkdir(parents=True)
    (project / "local").mkdir(parents=True)
    runner = project / "local" / "run-local-sql.sh"
    runner.write_text(runner_source(), encoding="utf-8")
    runner.chmod(0o755)
    for name in OVERLAY_FILES:
        (project / "local" / name).write_text(template(name), encoding="utf-8")
    (project / "sql" / "job.sql").write_text(
        "CREATE TABLE orders_source (order_id BIGINT, amount DECIMAL(10, 2))\n"
        "  WITH ('connector' = 'kafka', 'topic' = 'orders');\n"
        "CREATE TABLE orders_sink (order_id BIGINT, amount DECIMAL(10, 2))\n"
        "  WITH ('connector' = 'kafka', 'topic' = 'orders-agg');\n"
        "INSERT INTO orders_sink SELECT order_id, amount FROM orders_source;\n",
        encoding="utf-8",
    )
    return project


def run(project: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(project.parent),
        "FLINK_HOME": str(project.parent / "flink"),
        "ARGV_LOG": str(project.parent / "argv.log"),
        "INIT_COPY": str(project.parent / "init.sql"),
    }
    environment.update(env)
    return subprocess.run(
        ["bash", "local/run-local-sql.sh", *args],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
    )


def overlay_args(project: Path, *, sinks: bool = True) -> list[str]:
    args = [
        "--job",
        "sql/job.sql",
        "--session",
        "local/local-session.sql",
        "--sources",
        "local/local-sources.sql",
    ]
    if sinks:
        args += ["--sinks", "local/local-sinks.sql"]
    return args


def test_rendered_overlay_passes_every_guard_and_reaches_the_sql_client(workspace: Path):
    result = run(workspace, *overlay_args(workspace), "--expect-flink-version", "1.20")
    assert result.returncode == 0, result.stderr
    argv = (workspace.parent / "argv.log").read_text(encoding="utf-8")
    assert " -f sql/job.sql" in argv
    assert re.search(r"-i \S+flink-local-init\.\S+", argv), argv
    assert "sink shadows verified: orders_sink" in result.stderr


def test_runner_concatenates_the_overlays_in_load_order(workspace: Path):
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 0, result.stderr
    init_sql = (workspace.parent / "init.sql").read_text(encoding="utf-8")
    assert init_sql.index("execution.target") < init_sql.index(
        "CREATE TEMPORARY TABLE orders_source"
    )
    assert init_sql.index("CREATE TEMPORARY TABLE orders_source") < init_sql.index(
        "CREATE TEMPORARY TABLE orders_sink"
    )


def test_runner_removes_the_temporary_init_file(workspace: Path):
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 0, result.stderr
    argv = (workspace.parent / "argv.log").read_text(encoding="utf-8")
    init_path = Path(re.search(r"-i (\S+)", argv).group(1))
    assert not init_path.exists(), init_path


def test_runner_refuses_a_job_whose_sink_has_no_local_shadow(workspace: Path):
    result = run(workspace, *overlay_args(workspace, sinks=False))
    assert result.returncode == 4
    assert "orders_sink" in result.stderr
    assert "--sinks" in result.stderr


def test_runner_refuses_a_non_local_sink_connector(workspace: Path):
    (workspace / "local" / "local-sinks.sql").write_text(
        "CREATE TEMPORARY TABLE orders_sink (order_id BIGINT) WITH (\n"
        "  'connector' = 'kafka',\n"
        "  'topic' = 'orders-agg'\n"
        ");\n",
        encoding="utf-8",
    )
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 4
    assert "kafka" in result.stderr


def test_runner_refuses_a_sink_path_outside_the_local_filesystem(workspace: Path):
    (workspace / "local" / "local-sinks.sql").write_text(
        "CREATE TEMPORARY TABLE orders_sink (order_id BIGINT) WITH (\n"
        "  'connector' = 'filesystem',\n"
        "  'path' = 's3://prod-bucket/orders',\n"
        "  'format' = 'json'\n"
        ");\n",
        encoding="utf-8",
    )
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 4
    assert "s3://prod-bucket/orders" in result.stderr


def test_runner_refuses_a_ctas_that_no_shadow_can_intercept(workspace: Path):
    (workspace / "sql" / "job.sql").write_text(
        "CREATE TABLE orders_sink WITH ('connector' = 'kafka', 'topic' = 'orders-agg')\n"
        "  AS SELECT order_id, amount FROM orders_source;\n",
        encoding="utf-8",
    )
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 4
    assert "AS SELECT" in result.stderr


def test_runner_accepts_a_local_relative_sink_path(workspace: Path):
    (workspace / "local" / "local-sinks.sql").write_text(
        "CREATE TEMPORARY TABLE orders_sink (order_id BIGINT) WITH (\n"
        "  'connector' = 'filesystem',\n"
        "  'path' = 'target/local-out/orders',\n"
        "  'format' = 'json'\n"
        ");\n",
        encoding="utf-8",
    )
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 0, result.stderr


def test_runner_ignores_commented_out_variants(workspace: Path):
    (workspace / "local" / "local-sinks.sql").write_text(
        "CREATE TEMPORARY TABLE orders_sink (order_id BIGINT) WITH ('connector' = 'print');\n"
        "-- CREATE TEMPORARY TABLE orders_sink (order_id BIGINT) WITH (\n"
        "--   'connector' = 'kafka',\n"
        "--   'path' = 's3://prod-bucket/orders'\n"
        "-- );\n",
        encoding="utf-8",
    )
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "session_sql, expected_in_stderr",
    [
        ("SET 'table.dml-sync' = 'true';\n", "execution.target"),
        ("SET 'execution.target' = 'local';\n", "table.dml-sync"),
        (
            "-- SET 'execution.target' = 'local';\nSET 'table.dml-sync' = 'true';\n",
            "execution.target",
        ),
    ],
)
def test_runner_requires_the_session_overlay_to_pin_local_execution(
    workspace: Path, session_sql: str, expected_in_stderr: str
):
    (workspace / "local" / "local-session.sql").write_text(session_sql, encoding="utf-8")
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 4
    assert expected_in_stderr in result.stderr


def test_runner_refuses_a_distribution_that_is_not_the_production_version(workspace: Path):
    result = run(workspace, *overlay_args(workspace), "--expect-flink-version", "1.19")
    assert result.returncode == 3
    assert "1.20.1" in result.stderr and "1.19" in result.stderr


def test_runner_accepts_an_exact_patch_version_match(workspace: Path):
    result = run(workspace, *overlay_args(workspace), "--expect-flink-version", "1.20.1")
    assert result.returncode == 0, result.stderr


def test_runner_needs_a_local_distribution(workspace: Path):
    result = run(workspace, *overlay_args(workspace), FLINK_HOME="")
    assert result.returncode == 3
    assert "FLINK_HOME" in result.stderr

    result = run(workspace, *overlay_args(workspace), FLINK_HOME=str(workspace / "nope"))
    assert result.returncode == 3
    assert "sql-client.sh" in result.stderr


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the credential guard")
def test_runner_refuses_a_tracked_overlay_that_carries_a_credential(workspace: Path):
    subprocess.run(["git", "init", "-q", "."], cwd=workspace, check=True)
    (workspace / "local" / "local-sources.sql").write_text(
        "CREATE TEMPORARY TABLE orders_source (order_id BIGINT) WITH (\n"
        "  'connector' = 'jdbc',\n"
        "  'url' = 'jdbc:mysql://db-dev.internal:3306/shop',\n"
        "  'table-name' = 'orders',\n"
        "  'username' = 'orders_ro',\n"
        "  'password' = 'not-a-real-password'\n"
        ");\n",
        encoding="utf-8",
    )
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 4
    assert ".gitignore" in result.stderr

    (workspace / ".gitignore").write_text("local/local-sources.sql\n", encoding="utf-8")
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the credential guard")
def test_credential_guard_reads_only_active_statements(workspace: Path):
    """The shipped template documents a password in a comment; that must not fail a run."""
    subprocess.run(["git", "init", "-q", "."], cwd=workspace, check=True)
    assert "password" in (workspace / "local" / "local-sources.sql").read_text(encoding="utf-8")
    result = run(workspace, *overlay_args(workspace))
    assert result.returncode == 0, result.stderr


def test_runner_reports_the_sql_client_exit_code(workspace: Path):
    result = run(workspace, *overlay_args(workspace), FAKE_RC="7")
    assert result.returncode == 7
    assert "exited with 7" in result.stderr


@pytest.mark.skipif(
    shutil.which("timeout") is None and shutil.which("gtimeout") is None,
    reason="no timeout/gtimeout available to cap the run",
)
def test_runner_maps_a_wall_clock_timeout_to_actionable_advice(workspace: Path):
    fake_client = workspace.parent / "flink" / "bin" / "sql-client.sh"
    fake_client.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    fake_client.chmod(0o755)
    result = run(workspace, *overlay_args(workspace), "--timeout", "1")
    assert result.returncode == 5
    assert "scan.bounded.mode" in result.stderr


def test_runner_runs_several_job_files_in_the_given_order(workspace: Path):
    (workspace / "sql" / "ddl.sql").write_text(
        "CREATE TABLE orders_sink (order_id BIGINT) WITH ('connector' = 'kafka');\n",
        encoding="utf-8",
    )
    (workspace / "sql" / "dml.sql").write_text(
        "INSERT INTO orders_sink SELECT order_id FROM orders_source;\n", encoding="utf-8"
    )
    result = run(
        workspace,
        "--job",
        "sql/ddl.sql",
        "--job",
        "sql/dml.sql",
        "--session",
        "local/local-session.sql",
        "--sinks",
        "local/local-sinks.sql",
    )
    assert result.returncode == 0, result.stderr
    assert "sql/ddl.sql,sql/dml.sql" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--job", "sql/job.sql"],
        ["--session", "local/local-session.sql"],
        ["--job", "sql/missing.sql", "--session", "local/local-session.sql"],
        ["--job", "sql/job.sql", "--session", "local/local-session.sql", "--timeout", "soon"],
        ["--bogus"],
        ["--job"],
    ],
)
def test_runner_rejects_unusable_invocations(workspace: Path, args: list[str]):
    assert run(workspace, *args).returncode == 2


def test_runner_rejects_a_missing_connector_jar(workspace: Path):
    result = run(workspace, *overlay_args(workspace), "--jar", "lib/absent.jar")
    assert result.returncode == 3
    assert "absent.jar" in result.stderr
