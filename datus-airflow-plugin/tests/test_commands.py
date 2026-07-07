"""Command handlers end-to-end against the fake HTTP session (real parser)."""

from __future__ import annotations

import json

import pytest

from datus_airflow_plugin.errors import PluginError, UsageError

from conftest import FakeResponse, paged


# ------------------------------------------------------------------- dags


def test_dags_list_table_and_filters(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/dags",
        FakeResponse(json_data=paged("dags", [
            {"dag_id": "etl", "fileloc": "/dags/etl.py", "owners": ["airflow"], "is_paused": False},
        ])),
    )
    assert run_cli(["dags", "list", "-p", "etl%", "--paused"]) == 0
    out = capsys.readouterr().out
    assert "etl" in out and "dag_id" in out
    params = fake_session.calls[0]["params"]
    assert params["dag_id_pattern"] == "etl%"
    assert params["paused"] == "true"


def test_dags_trigger_sends_nullable_logical_date_and_conf(run_cli, fake_session, capsys):
    fake_session.add(
        "POST",
        "/api/v2/dags/etl/dagRuns",
        FakeResponse(json_data={"dag_run_id": "manual__1", "state": "queued"}),
    )
    rc = run_cli(["dags", "trigger", "etl", "-c", '{"k": 1}', "--note", "from test"])
    assert rc == 0
    body = fake_session.calls[0]["json"]
    assert body["logical_date"] is None  # required-but-nullable field always present
    assert body["conf"] == {"k": 1}
    assert body["note"] == "from test"
    assert "manual__1" in capsys.readouterr().out


def test_dags_trigger_wait_polls_until_terminal(run_cli, fake_session, monkeypatch, capsys):
    monkeypatch.setattr("time.sleep", lambda s: None)
    fake_session.add(
        "POST", "/api/v2/dags/etl/dagRuns", FakeResponse(json_data={"dag_run_id": "r1", "state": "queued"})
    )
    fake_session.add(
        "GET",
        "/api/v2/dags/etl/dagRuns/r1",
        [
            FakeResponse(json_data={"state": "running"}),
            FakeResponse(json_data={"state": "failed"}),
        ],
    )
    rc = run_cli(["dags", "trigger", "etl", "--wait", "--interval", "0"])
    assert rc == 1  # failed run -> exit 1
    assert "failed" in capsys.readouterr().err  # wait progress goes to stderr


def test_dags_pause_uses_update_mask(run_cli, fake_session, capsys):
    fake_session.add(
        "PATCH", "/api/v2/dags/etl", FakeResponse(json_data={"dag_id": "etl", "is_paused": True})
    )
    assert run_cli(["dags", "pause", "etl"]) == 0
    call = fake_session.calls[0]
    assert call["params"] == {"update_mask": "is_paused"}
    assert call["json"] == {"is_paused": True}


def test_dags_state_prints_run_state(run_cli, fake_session, capsys):
    fake_session.add(
        "GET", "/api/v2/dags/etl/dagRuns/r1", FakeResponse(json_data={"state": "success"})
    )
    assert run_cli(["dags", "state", "etl", "r1"]) == 0
    assert capsys.readouterr().out.strip() == "success"


def test_dags_delete_requires_confirmation(run_cli, fake_session):
    with pytest.raises(UsageError):  # stdin is not a tty in tests
        run_cli(["dags", "delete", "etl"])
    fake_session.add("DELETE", "/api/v2/dags/etl", FakeResponse(204, text=""))
    assert run_cli(["dags", "delete", "etl", "-y"]) == 0


def test_dags_show_renders_tree(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/dags/etl/tasks",
        FakeResponse(json_data={"tasks": [
            {"task_id": "extract", "operator_name": "PythonOperator", "downstream_task_ids": ["load"]},
            {"task_id": "load", "operator_name": "PythonOperator", "downstream_task_ids": []},
        ], "total_entries": 2}),
    )
    assert run_cli(["dags", "show", "etl"]) == 0
    out = capsys.readouterr().out
    assert "└── extract [PythonOperator]" in out
    assert "    └── load [PythonOperator]" in out


def test_dags_run_id_is_url_quoted(run_cli, fake_session):
    run_id = "manual__2026-07-05T00:00:00+00:00"
    quoted = "manual__2026-07-05T00%3A00%3A00%2B00%3A00"
    fake_session.add(
        "GET", f"/api/v2/dags/etl/dagRuns/{quoted}", FakeResponse(json_data={"state": "success"})
    )
    assert run_cli(["dags", "state", "etl", run_id]) == 0


# ------------------------------------------------------------------- tasks


def test_tasks_clear_expands_regex_and_confirms_with_yes(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/dags/etl/tasks",
        FakeResponse(json_data={"tasks": [
            {"task_id": "load_a"}, {"task_id": "load_b"}, {"task_id": "extract"},
        ], "total_entries": 3}),
    )
    fake_session.add(
        "POST",
        "/api/v2/dags/etl/clearTaskInstances",
        FakeResponse(json_data=paged("task_instances", [
            {"task_id": "load_a", "dag_run_id": "r1", "state": "failed"},
        ])),
    )
    rc = run_cli(["tasks", "clear", "etl", "-t", "^load_", "-r", "r1", "--only-failed", "-y"])
    assert rc == 0
    dry, real = fake_session.calls_to("POST", "/api/v2/dags/etl/clearTaskInstances")
    assert dry["json"]["dry_run"] is True
    assert real["json"]["dry_run"] is False
    assert real["json"]["task_ids"] == ["load_a", "load_b"]
    assert real["json"]["only_failed"] is True
    assert real["json"]["dag_run_id"] == "r1"


def test_tasks_logs_fetches_latest_try_and_formats_events(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/dags/etl/dagRuns/r1/taskInstances/load",
        FakeResponse(json_data={"try_number": 2}),
    )
    fake_session.add(
        "GET",
        "/api/v2/dags/etl/dagRuns/r1/taskInstances/load/logs/2",
        FakeResponse(json_data={
            "content": [
                {"timestamp": "2026-07-05T00:00:00Z", "event": "starting", "level": "info"},
            ],
            "continuation_token": None,
        }),
    )
    assert run_cli(["tasks", "logs", "etl", "r1", "load"]) == 0
    out = capsys.readouterr().out
    assert "[2026-07-05T00:00:00Z] starting level=info" in out


def test_tasks_state_with_map_index_uses_mapped_path(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/dags/etl/dagRuns/r1/taskInstances/load/3",
        FakeResponse(json_data={"state": "success"}),
    )
    assert run_cli(["tasks", "state", "etl", "r1", "load", "--map-index", "3"]) == 0
    assert capsys.readouterr().out.strip() == "success"


# --------------------------------------------------------------- variables


def test_variables_set_upserts_on_conflict(run_cli, fake_session, capsys):
    fake_session.add("POST", "/api/v2/variables", FakeResponse(409, json_data={"detail": "exists"}))
    fake_session.add("PATCH", "/api/v2/variables/ENV", FakeResponse(json_data={"key": "ENV"}))
    assert run_cli(["variables", "set", "ENV", "prod"]) == 0
    assert "updated variable ENV" in capsys.readouterr().out
    patch = fake_session.calls_to("PATCH", "/api/v2/variables/ENV")[0]
    assert patch["json"]["value"] == "prod"


def test_variables_set_json_value(run_cli, fake_session):
    fake_session.add("POST", "/api/v2/variables", FakeResponse(json_data={"key": "cfg"}))
    assert run_cli(["variables", "set", "cfg", '{"a": 1}', "--json"]) == 0
    assert fake_session.calls[0]["json"]["value"] == {"a": 1}


def test_variables_get_missing_with_default(run_cli, fake_session, capsys):
    fake_session.add("GET", "/api/v2/variables/absent", FakeResponse(404, json_data={"detail": "nope"}))
    assert run_cli(["variables", "get", "absent", "-d", "fallback"]) == 0
    assert capsys.readouterr().out.strip() == "fallback"


def test_variables_export_and_import_round_trip(run_cli, fake_session, tmp_path, capsys):
    fake_session.add(
        "GET",
        "/api/v2/variables",
        FakeResponse(json_data=paged("variables", [
            {"key": "A", "value": "1"}, {"key": "B", "value": "2"},
        ])),
    )
    out_file = tmp_path / "vars.json"
    assert run_cli(["variables", "export", str(out_file)]) == 0
    assert json.loads(out_file.read_text()) == {"A": "1", "B": "2"}

    fake_session.add("POST", "/api/v2/variables", FakeResponse(409, json_data={"detail": "exists"}))
    fake_session.add("PATCH", "/api/v2/variables/A", FakeResponse(json_data={}))
    fake_session.add("PATCH", "/api/v2/variables/B", FakeResponse(json_data={}))
    assert run_cli(["variables", "import", str(out_file)]) == 0
    assert "2 updated" in capsys.readouterr().out


# ------------------------------------------------------------- connections


def test_connections_get_masks_password_by_default(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/connections/pg",
        FakeResponse(json_data={"connection_id": "pg", "conn_type": "postgres", "password": "hunter2"}),
    )
    assert run_cli(["connections", "get", "pg"]) == 0
    out = capsys.readouterr().out
    assert "hunter2" not in out and "***" in out

    assert run_cli(["connections", "get", "pg", "--show-secrets", "-o", "json"]) == 0
    assert "hunter2" in capsys.readouterr().out


def test_connections_add_from_uri(run_cli, fake_session):
    fake_session.add("POST", "/api/v2/connections", FakeResponse(json_data={}))
    rc = run_cli(["connections", "add", "pg", "--conn-uri", "postgres://u:p@h:5432/db"])
    assert rc == 0
    body = fake_session.calls[0]["json"]
    assert body == {
        "connection_id": "pg", "conn_type": "postgres", "host": "h",
        "login": "u", "password": "p", "port": 5432, "schema": "db",
    }


def test_connections_test_disabled_gets_hint(run_cli, fake_session):
    fake_session.add(
        "POST", "/api/v2/connections/test", FakeResponse(403, json_data={"detail": "disabled"})
    )
    with pytest.raises(PluginError) as exc:
        run_cli(["connections", "test", "--conn-uri", "http://h"])
    assert "TEST_CONNECTION" in str(exc.value)


def test_connections_export_env_format(run_cli, fake_session, tmp_path):
    fake_session.add(
        "GET",
        "/api/v2/connections",
        FakeResponse(json_data=paged("connections", [
            {"connection_id": "pg", "conn_type": "postgres", "host": "h", "login": "u",
             "password": "p", "schema": "db", "port": 5432, "extra": None, "description": None},
        ])),
    )
    out_file = tmp_path / "conns.env"
    assert run_cli(["connections", "export", str(out_file)]) == 0
    assert out_file.read_text().strip() == "pg=postgres://u:p@h:5432/db"


# ------------------------------------------------------------------- pools


def test_pools_set_creates_then_updates(run_cli, fake_session, capsys):
    fake_session.add("POST", "/api/v2/pools", FakeResponse(409, json_data={"detail": "exists"}))
    fake_session.add("PATCH", "/api/v2/pools/etl", FakeResponse(json_data={}))
    assert run_cli(["pools", "set", "etl", "16", "etl pool"]) == 0
    patch = fake_session.calls_to("PATCH", "/api/v2/pools/etl")[0]
    assert patch["json"] == {"slots": 16, "description": "etl pool", "include_deferred": False}
    assert patch["params"]["update_mask"] == "slots,description,include_deferred"


# ---------------------------------------------------------------- backfill


def test_backfill_create_dry_run_lists_dates(run_cli, fake_session, capsys):
    fake_session.add(
        "POST",
        "/api/v2/backfills/dry_run",
        FakeResponse(json_data={"backfills": [{"logical_date": "2026-01-01T00:00:00Z"}]}),
    )
    rc = run_cli([
        "backfill", "create", "--dag-id", "etl",
        "--from-date", "2026-01-01", "--to-date", "2026-01-02", "--dry-run",
    ])
    assert rc == 0
    body = fake_session.calls[0]["json"]
    assert body["dag_id"] == "etl"
    assert body["from_date"].startswith("2026-01-01T00:00:00")
    assert "would create 1 run(s)" in capsys.readouterr().out


# -------------------------------------------------------------------- misc


def test_jobs_check_exit_codes(run_cli, fake_session, capsys):
    fake_session.add("GET", "/api/v2/jobs", FakeResponse(json_data=paged("jobs", [])))
    assert run_cli(["jobs", "check"]) == 1

    fake_session.add(
        "GET",
        "/api/v2/jobs",
        FakeResponse(json_data=paged("jobs", [
            {"id": 1, "job_type": "SchedulerJob", "state": "running", "hostname": "h"},
        ])),
    )
    assert run_cli(["jobs", "check", "--job-type", "SchedulerJob"]) == 0
    assert fake_session.calls[-1]["params"]["is_alive"] == "true"


def test_health_reports_unhealthy_with_exit_1(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/monitor/health",
        FakeResponse(json_data={
            "metadatabase": {"status": "healthy"},
            "scheduler": {"status": "unhealthy", "latest_scheduler_heartbeat": "2026-07-05T00:00:00Z"},
        }),
    )
    assert run_cli(["health"]) == 1
    out = capsys.readouterr().out
    assert "scheduler" in out and "unhealthy" in out


def test_config_get_value_unwraps_source_tuples(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/config/section/core/option/parallelism",
        FakeResponse(json_data={"sections": [
            {"name": "core", "options": [{"key": "parallelism", "value": ["32", "airflow.cfg"]}]},
        ]}),
    )
    assert run_cli(["config", "get-value", "core", "parallelism"]) == 0
    assert capsys.readouterr().out.strip() == "32"


def test_version_prints_both_versions(run_cli, fake_session, capsys):
    fake_session.add(
        "GET", "/api/v2/version", FakeResponse(json_data={"version": "3.1.6", "git_version": "abc"})
    )
    assert run_cli(["version"]) == 0
    out = capsys.readouterr().out
    assert "3.1.6" in out and "datus-airflow-plugin" in out


# ------------------------------------------------------------------ assets


def test_assets_materialize_resolves_by_name(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/api/v2/assets",
        FakeResponse(json_data=paged("assets", [
            {"id": 7, "name": "orders", "uri": "s3://data/orders", "group": "asset"},
        ])),
    )
    fake_session.add(
        "POST", "/api/v2/assets/7/materialize", FakeResponse(json_data={"dag_run_id": "r9"})
    )
    assert run_cli(["assets", "materialize", "--name", "orders"]) == 0
    assert "r9" in capsys.readouterr().out
