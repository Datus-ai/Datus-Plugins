"""Command tests for datus emr."""

from __future__ import annotations

import gzip
import io


def test_clusters_list(run_cli, clients, capsys):
    clients["emr"].set_pages(
        "list_clusters",
        [{"Clusters": [{"Id": "j-1", "Name": "analytics", "Status": {"State": "WAITING"}}]}],
    )
    assert run_cli(["clusters", "list"]) == 0
    out = capsys.readouterr().out
    assert "j-1" in out and "WAITING" in out


def test_steps_add_command_wait_success(run_cli, clients, capsys):
    clients["emr"].set("add_job_flow_steps", {"StepIds": ["s-1"]})
    clients["emr"].set(
        "describe_step",
        [
            {"Step": {"Status": {"State": "RUNNING"}}},
            {"Step": {"Status": {"State": "COMPLETED"}}},
        ],
    )
    rc = run_cli(["steps", "add", "j-1", "--name", "load", "--command", "spark-submit s3://b/job.py", "--wait", "--interval", "0"])
    assert rc == 0
    assert "COMPLETED" in capsys.readouterr().out
    step = clients["emr"].calls_to("add_job_flow_steps")[0]["kwargs"]["Steps"][0]
    assert step["HadoopJarStep"]["Jar"] == "command-runner.jar"
    assert step["HadoopJarStep"]["Args"] == ["spark-submit", "s3://b/job.py"]


def test_steps_add_wait_failed_returns_1(run_cli, clients):
    clients["emr"].set("add_job_flow_steps", {"StepIds": ["s-2"]})
    clients["emr"].set("describe_step", {"Step": {"Status": {"State": "FAILED"}}})
    assert run_cli(["steps", "add", "j-1", "--name", "x", "--command", "true", "--wait", "--interval", "0"]) == 1


def test_steps_add_requires_jar_or_command(run_cli, clients):
    assert run_cli(["steps", "add", "j-1", "--name", "x"]) == 2  # UsageError


def test_steps_add_uses_default_cluster(run_cli, clients):
    clients["emr"].set("add_job_flow_steps", {"StepIds": ["s-3"]})
    rc = run_cli(["steps", "add", "--name", "x", "--command", "true"], {"region": "us-east-1", "cluster_id": "j-default"})
    assert rc == 0
    assert clients["emr"].calls_to("add_job_flow_steps")[0]["kwargs"]["JobFlowId"] == "j-default"


def test_steps_logs_reads_gzip_from_s3(run_cli, clients, capsys):
    clients["s3"].set("get_object", {"Body": io.BytesIO(gzip.compress(b"line1\nline2\n"))})
    profile = {"region": "us-east-1", "log_uri": "s3://logs-bucket/emr/"}
    assert run_cli(["steps", "logs", "j-1", "s-1"], profile) == 0
    assert "line1" in capsys.readouterr().out
    key = clients["s3"].calls_to("get_object")[0]["kwargs"]["Key"]
    assert key == "emr/j-1/steps/s-1/stdout.gz"


def test_steps_logs_needs_log_uri(run_cli, clients):
    assert run_cli(["steps", "logs", "j-1", "s-1"]) == 2  # UsageError: no log_uri


def test_steps_cancel(run_cli, clients):
    assert run_cli(["steps", "cancel", "j-1", "s-1"]) == 0
    assert clients["emr"].calls_to("cancel_steps")[0]["kwargs"]["StepIds"] == ["s-1"]
