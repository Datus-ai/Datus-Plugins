"""Command tests: assert exit codes, output, and the kwargs sent to boto3."""

from __future__ import annotations


def test_logs_groups_paginates_and_filters(run_cli, clients, capsys):
    clients["logs"].set_pages(
        "describe_log_groups",
        [{"logGroups": [{"logGroupName": "/aws/glue/jobs", "storedBytes": 42, "retentionInDays": 7}]}],
    )
    assert run_cli(["logs", "groups", "-p", "/aws/glue"]) == 0
    out = capsys.readouterr().out
    assert "/aws/glue/jobs" in out and "logGroupName" in out
    assert clients["logs"].calls[0]["kwargs"]["logGroupNamePrefix"] == "/aws/glue"


def test_logs_get_renders_events_as_table(run_cli, clients, capsys):
    clients["logs"].set(
        "get_log_events",
        {"events": [{"timestamp": 1_700_000_000_000, "message": "hello\n"}]},
    )
    assert run_cli(["logs", "get", "/g", "stream-1"]) == 0
    out = capsys.readouterr().out
    assert "hello" in out and "timestamp" in out
    kwargs = clients["logs"].calls[0]["kwargs"]
    assert kwargs["logGroupName"] == "/g" and kwargs["logStreamName"] == "stream-1"


def test_logs_tail_oneshot_prints_lines(run_cli, clients, capsys):
    clients["logs"].set(
        "filter_log_events",
        {"events": [{"timestamp": 1_700_000_000_000, "message": "ERROR boom"}]},
    )
    assert run_cli(["logs", "tail", "/g", "--filter", "ERROR", "--since", "5m"]) == 0
    assert "ERROR boom" in capsys.readouterr().out
    assert clients["logs"].calls[0]["kwargs"]["filterPattern"] == "ERROR"


def test_logs_insights_polls_until_complete(run_cli, clients, capsys):
    clients["logs"].set("start_query", {"queryId": "q-1"})
    clients["logs"].set(
        "get_query_results",
        [
            {"status": "Running", "results": []},
            {"status": "Complete", "results": [[{"field": "n", "value": "5"}]]},
        ],
    )
    rc = run_cli(["logs", "insights", "/g", "-q", "stats count()", "--start", "now", "--interval", "0"])
    assert rc == 0
    assert "n" in capsys.readouterr().out
    assert clients["logs"].calls[0]["kwargs"]["queryString"] == "stats count()"


def test_logs_insights_noncomplete_is_runtime_error(run_cli, clients, capsys):
    clients["logs"].set("start_query", {"queryId": "q-2"})
    clients["logs"].set("get_query_results", {"status": "Failed", "results": []})
    rc = run_cli(["logs", "insights", "/g", "-q", "x", "--start", "now", "--interval", "0"])
    assert rc == 1


def test_metrics_list_and_get(run_cli, clients, capsys):
    clients["cloudwatch"].set_pages(
        "list_metrics",
        [{"Metrics": [{"Namespace": "AWS/Lambda", "MetricName": "Errors", "Dimensions": []}]}],
    )
    assert run_cli(["metrics", "list", "--namespace", "AWS/Lambda"]) == 0
    assert "Errors" in capsys.readouterr().out

    clients["cloudwatch"].set(
        "get_metric_statistics",
        {"Datapoints": [{"Timestamp": "2026-07-01T00:00:00Z", "Sum": 3.0, "Unit": "Count"}]},
    )
    rc = run_cli(
        ["metrics", "get", "--namespace", "AWS/Lambda", "--name", "Errors",
         "--stat", "Sum", "--start", "now", "-d", "FunctionName=fn"]
    )
    assert rc == 0
    kwargs = clients["cloudwatch"].calls_to("get_metric_statistics")[0]["kwargs"]
    assert kwargs["Dimensions"] == [{"Name": "FunctionName", "Value": "fn"}]
    assert kwargs["Statistics"] == ["Sum"]


def test_alarms_list_get_and_set_state(run_cli, clients, capsys):
    clients["cloudwatch"].set_pages(
        "describe_alarms",
        [{"MetricAlarms": [{"AlarmName": "cpu-high", "StateValue": "OK", "MetricName": "CPU"}]}],
    )
    assert run_cli(["alarms", "list", "--state", "OK"]) == 0
    assert "cpu-high" in capsys.readouterr().out
    assert clients["cloudwatch"].calls[0]["kwargs"]["StateValue"] == "OK"

    clients["cloudwatch"].set("describe_alarms", {"MetricAlarms": [{"AlarmName": "cpu-high", "StateValue": "OK"}]})
    assert run_cli(["alarms", "get", "cpu-high"]) == 0

    assert run_cli(["alarms", "set-state", "cpu-high", "--state", "ALARM", "--reason", "test"]) == 0
    kwargs = clients["cloudwatch"].calls_to("set_alarm_state")[0]["kwargs"]
    assert kwargs["StateValue"] == "ALARM" and kwargs["AlarmName"] == "cpu-high"


def test_alarms_get_not_found_is_runtime_error(run_cli, clients):
    clients["cloudwatch"].set("describe_alarms", {"MetricAlarms": [], "CompositeAlarms": []})
    assert run_cli(["alarms", "get", "missing"]) == 1


def test_dashboards_list_and_get(run_cli, clients, capsys):
    clients["cloudwatch"].set_pages(
        "list_dashboards", [{"DashboardEntries": [{"DashboardName": "ops", "Size": 100}]}]
    )
    assert run_cli(["dashboards", "list"]) == 0
    assert "ops" in capsys.readouterr().out

    clients["cloudwatch"].set("get_dashboard", {"DashboardName": "ops", "DashboardBody": '{"widgets":[]}'})
    assert run_cli(["dashboards", "get", "ops", "-o", "json"]) == 0
    out = capsys.readouterr().out
    assert '"widgets"' in out
