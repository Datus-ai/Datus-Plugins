"""Handler behavior against a fake Statsig API (no network, no datus)."""

from __future__ import annotations

import json

import pytest
from conftest import BASE_URL, FakeResponse, paged, single

from datus_statsig_plugin.cli import main
from datus_statsig_plugin.client import StatsigClient
from datus_statsig_plugin.errors import ApiError


def test_metrics_list_paginates_and_sends_auth(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/console/v1/metrics/list",
        [
            paged([{"id": "a", "name": "A", "type": "count"}], next_page=2),
            paged([{"id": "b", "name": "B", "type": "mean"}], next_page=None),
        ],
    )
    rc = run_cli(["metrics", "list"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in out] == ["a", "b"]
    call = fake_session.calls_to("GET", "/console/v1/metrics/list")[0]
    assert call["headers"]["STATSIG-API-KEY"] == "test-console-key"
    assert call["headers"]["STATSIG-API-VERSION"] == "20240601"


def test_metrics_get_by_id(run_cli, fake_session, capsys):
    fake_session.add("GET", "/console/v1/metrics/m1", single({"id": "m1", "name": "signups"}))
    rc = run_cli(["metrics", "get", "m1"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["name"] == "signups"


def test_metrics_get_by_name_and_type(run_cli, fake_session):
    fake_session.add("GET", "/console/v1/metrics/signups/count", single({"id": "m1"}))
    rc = run_cli(["metrics", "get", "--name", "signups", "--type", "count"])
    assert rc == 0
    assert fake_session.calls_to("GET", "/console/v1/metrics/signups/count")


def test_metrics_get_requires_id_or_name_type():
    rc = main(["metrics", "get"], {"api_key": "x"})
    assert rc == 2


def test_metrics_sql(run_cli, fake_session, capsys):
    fake_session.add("GET", "/console/v1/metrics/m1/sql", single({"sql": "SELECT 1"}))
    rc = run_cli(["metrics", "sql", "m1"])
    assert rc == 0
    assert "SELECT 1" in capsys.readouterr().out


def test_metrics_create_dry_run_sets_flag(run_cli, fake_session):
    fake_session.add("POST", "/console/v1/metrics", single({"id": "new"}))
    rc = run_cli(["metrics", "create", "--json", '{"name":"m","type":"count"}', "--dry-run"])
    assert rc == 0
    body = fake_session.calls_to("POST", "/console/v1/metrics")[0]["json"]
    assert body["name"] == "m" and body["type"] == "count" and body["dryRun"] is True


def test_metric_source_create_from_file(run_cli, fake_session, tmp_path):
    fake_session.add("POST", "/console/v1/metrics/metric_source", single({"name": "purchases"}))
    body_file = tmp_path / "src.json"
    body_file.write_text(
        json.dumps(
            {
                "name": "purchases",
                "sql": "SELECT user_id, ts FROM t",
                "timestampColumn": "ts",
                "idTypeMapping": {"userID": "user_id"},
            }
        )
    )
    rc = run_cli(["metric-source", "create", "--json-file", str(body_file)])
    assert rc == 0
    assert fake_session.calls_to("POST", "/console/v1/metrics/metric_source")


def test_experiments_pulse_sends_control_and_test(run_cli, fake_session, capsys):
    fake_session.add(
        "GET",
        "/console/v1/experiments/exp1/pulse_results",
        single({"primaryMetrics": []}),
    )
    rc = run_cli(["experiments", "pulse", "exp1", "--control", "ctrl", "--test", "treat"])
    assert rc == 0
    call = fake_session.calls_to("GET", "/console/v1/experiments/exp1/pulse_results")[0]
    assert call["params"]["control"] == "ctrl"
    assert call["params"]["test"] == "treat"


def test_ingestion_backfill_body(run_cli, fake_session):
    fake_session.add("POST", "/console/v1/ingestion/backfill", single({"runID": "r1"}))
    rc = run_cli(
        [
            "ingestion", "backfill", "--type", "metrics", "--dataset", "d1",
            "--start", "2024-09-01", "--end", "2024-09-07",
        ]
    )
    assert rc == 0
    body = fake_session.calls_to("POST", "/console/v1/ingestion/backfill")[0]["json"]
    assert body["datestamp_start"] == "2024-09-01"
    assert body["datestamp_end"] == "2024-09-07"
    assert body["type"] == "metrics" and body["dataset"] == "d1"


def test_warehouse_update_requires_one_of(run_cli, fake_session, tmp_path):
    body_file = tmp_path / "conn.json"
    body_file.write_text(json.dumps({"unknown": {}}))
    with pytest.raises(Exception) as exc:
        run_cli(["warehouse-connections", "update", "--json-file", str(body_file)])
    assert "one of" in str(exc.value)


def test_warehouse_update_valid(run_cli, fake_session, tmp_path):
    fake_session.add("PATCH", "/console/v1/wh_connections", single({}))
    body_file = tmp_path / "conn.json"
    body_file.write_text(json.dumps({"snowflake": {"account": "x"}}))
    rc = run_cli(["warehouse-connections", "update", "--json-file", str(body_file)])
    assert rc == 0
    assert fake_session.calls_to("PATCH", "/console/v1/wh_connections")


def test_compact_output_is_single_line(run_cli, fake_session, capsys):
    fake_session.add("GET", "/console/v1/metrics/m1", single({"id": "m1", "name": "x"}))
    rc = run_cli(["metrics", "get", "m1", "--compact"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "\n" not in out and out.startswith("{")


def test_describe_unknown_command_is_usage_error():
    rc = main(["describe", "gates", "toggle"], {})
    assert rc == 2


def test_client_raises_on_401(fake_session, settings):
    fake_session.add("GET", "/console/v1/metrics/list", FakeResponse(401, {"message": "bad key"}))
    client = StatsigClient(settings, session=fake_session)
    with pytest.raises(ApiError) as exc:
        client.request("GET", "/metrics/list")
    assert exc.value.status_code == 401


def test_base_url_normalizes_console_suffix():
    from datus_statsig_plugin.config import Settings

    s = Settings.from_profile({"api_base_url": f"{BASE_URL}/console/v1/", "api_key": "k"})
    assert s.base_url == BASE_URL
