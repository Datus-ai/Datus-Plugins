"""Command tests for datus emr-serverless."""

from __future__ import annotations

PROFILE = {"region": "us-east-1", "application_id": "app1", "execution_role_arn": "arn:aws:iam::1:role/exec"}


def test_applications_list(run_cli, clients, capsys):
    clients["emr-serverless"].set_pages(
        "list_applications", [{"applications": [{"id": "app1", "name": "spark", "state": "STARTED", "type": "Spark"}]}]
    )
    assert run_cli(["applications", "list"]) == 0
    assert "app1" in capsys.readouterr().out


def test_applications_start(run_cli, clients, capsys):
    assert run_cli(["applications", "start", "app1"]) == 0
    assert clients["emr-serverless"].calls_to("start_application")[0]["kwargs"]["applicationId"] == "app1"


def test_jobs_run_wait_success(run_cli, clients, capsys):
    clients["emr-serverless"].set("start_job_run", {"jobRunId": "jr1"})
    clients["emr-serverless"].set(
        "get_job_run",
        [{"jobRun": {"state": "RUNNING"}}, {"jobRun": {"state": "SUCCESS"}}],
    )
    rc = run_cli(
        ["jobs", "run", "--entry-point", "s3://b/job.py", "--wait", "--interval", "0"], PROFILE
    )
    assert rc == 0
    assert "SUCCESS" in capsys.readouterr().out
    kwargs = clients["emr-serverless"].calls_to("start_job_run")[0]["kwargs"]
    assert kwargs["executionRoleArn"] == "arn:aws:iam::1:role/exec"
    assert kwargs["applicationId"] == "app1"
    assert kwargs["jobDriver"]["sparkSubmit"]["entryPoint"] == "s3://b/job.py"


def test_jobs_run_wait_failed_returns_1(run_cli, clients):
    clients["emr-serverless"].set("start_job_run", {"jobRunId": "jr2"})
    clients["emr-serverless"].set("get_job_run", {"jobRun": {"state": "FAILED", "stateDetails": "oom"}})
    assert run_cli(["jobs", "run", "--entry-point", "s3://b/job.py", "--wait", "--interval", "0"], PROFILE) == 1


def test_jobs_run_missing_role_is_usage_error(run_cli, clients):
    # no execution role in profile or flag
    assert run_cli(["jobs", "run", "app1", "--entry-point", "s3://b/job.py"], {"region": "us-east-1"}) == 2


def test_jobs_dashboard(run_cli, clients, capsys):
    clients["emr-serverless"].set("get_dashboard_for_job_run", {"url": "https://dashboard"})
    assert run_cli(["jobs", "dashboard", "app1", "jr1"]) == 0
    assert "https://dashboard" in capsys.readouterr().out


def test_jobs_cancel(run_cli, clients):
    assert run_cli(["jobs", "cancel", "app1", "jr1"]) == 0
    assert clients["emr-serverless"].calls_to("cancel_job_run")[0]["kwargs"]["jobRunId"] == "jr1"
