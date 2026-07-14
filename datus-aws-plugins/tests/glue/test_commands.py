"""Command tests for datus glue."""

from __future__ import annotations


def test_catalog_databases(run_cli, clients, capsys):
    clients["glue"].set_pages("get_databases", [{"DatabaseList": [{"Name": "sales", "Description": "d"}]}])
    assert run_cli(["catalog", "databases"]) == 0
    assert "sales" in capsys.readouterr().out


def test_catalog_show_renders_schema(run_cli, clients, capsys):
    clients["glue"].set(
        "get_table",
        {"Table": {
            "Name": "orders", "DatabaseName": "sales", "TableType": "EXTERNAL_TABLE",
            "StorageDescriptor": {
                "Location": "s3://lake/orders/",
                "Columns": [{"Name": "id", "Type": "bigint"}, {"Name": "amount", "Type": "double"}],
                "SerdeInfo": {"SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"},
            },
            "PartitionKeys": [{"Name": "dt", "Type": "string"}],
        }},
    )
    assert run_cli(["catalog", "show", "sales", "orders"]) == 0
    out = capsys.readouterr().out
    assert "orders" in out and "s3://lake/orders/" in out
    assert "id" in out and "bigint" in out and "dt (partition)" in out


def test_catalog_passes_catalog_id(run_cli, clients):
    clients["glue"].set_pages("get_databases", [{"DatabaseList": []}])
    assert run_cli(["catalog", "databases"], {"region": "us-east-1", "catalog_id": "999"}) == 0
    assert clients["glue"].calls[0]["kwargs"]["CatalogId"] == "999"


def test_catalog_delete_table_confirmation(run_cli, clients):
    assert run_cli(["catalog", "delete-table", "sales", "orders"]) == 2  # no tty, no -y
    assert run_cli(["catalog", "delete-table", "sales", "orders", "-y"]) == 0
    assert clients["glue"].calls_to("delete_table")[0]["kwargs"]["Name"] == "orders"


def test_crawlers_run_wait_polls_until_ready(run_cli, clients, capsys):
    clients["glue"].set(
        "get_crawler",
        [
            {"Crawler": {"State": "READY"}},  # pre-run marker (no LastCrawl)
            {"Crawler": {"State": "RUNNING"}},
            {"Crawler": {"State": "READY", "LastCrawl": {"StartTime": "t1", "Status": "SUCCEEDED"}}},
        ],
    )
    rc = run_cli(["crawlers", "run", "raw", "--wait", "--interval", "0"])
    assert rc == 0
    assert "SUCCEEDED" in capsys.readouterr().out
    assert clients["glue"].calls_to("start_crawler")


def test_crawlers_run_wait_failed_returns_1(run_cli, clients):
    clients["glue"].set(
        "get_crawler",
        [
            {"Crawler": {"State": "READY"}},
            {"Crawler": {"State": "READY", "LastCrawl": {"StartTime": "t2", "Status": "FAILED"}}},
        ],
    )
    assert run_cli(["crawlers", "run", "raw", "--wait", "--interval", "0"]) == 1


def test_jobs_run_wait_success(run_cli, clients, capsys):
    clients["glue"].set("start_job_run", {"JobRunId": "jr_1"})
    clients["glue"].set(
        "get_job_run",
        [
            {"JobRun": {"JobRunState": "RUNNING"}},
            {"JobRun": {"JobRunState": "SUCCEEDED"}},
        ],
    )
    rc = run_cli(["jobs", "run", "etl", "--args", '{"--k":"v"}', "--wait", "--interval", "0"])
    assert rc == 0
    assert "SUCCEEDED" in capsys.readouterr().out
    assert clients["glue"].calls_to("start_job_run")[0]["kwargs"]["Arguments"] == {"--k": "v"}


def test_jobs_run_wait_failed_returns_1(run_cli, clients):
    clients["glue"].set("start_job_run", {"JobRunId": "jr_2"})
    clients["glue"].set("get_job_run", {"JobRun": {"JobRunState": "FAILED", "ErrorMessage": "boom"}})
    assert run_cli(["jobs", "run", "etl", "--wait", "--interval", "0"]) == 1


def test_jobs_logs_reads_cloudwatch(run_cli, clients, capsys):
    clients["logs"].set(
        "get_log_events",
        {"events": [{"timestamp": 1_700_000_000_000, "message": "processing"}]},
    )
    assert run_cli(["jobs", "logs", "etl", "jr_1"]) == 0
    assert "processing" in capsys.readouterr().out
    assert clients["logs"].calls_to("get_log_events")[0]["kwargs"]["logGroupName"] == "/aws-glue/jobs/output"


def test_jobs_logs_error_group(run_cli, clients):
    clients["logs"].set("get_log_events", {"events": []})
    assert run_cli(["jobs", "logs", "etl", "jr_1", "--error"]) == 0
    assert clients["logs"].calls_to("get_log_events")[0]["kwargs"]["logGroupName"] == "/aws-glue/jobs/error"


def test_connections_get_masks_secrets(run_cli, clients, capsys):
    clients["glue"].set(
        "get_connection",
        {"Connection": {"Name": "pg", "ConnectionType": "JDBC",
                        "ConnectionProperties": {"USERNAME": "u", "PASSWORD": "hunter2"}}},
    )
    assert run_cli(["connections", "get", "pg"]) == 0
    out = capsys.readouterr().out
    assert "hunter2" not in out and "***" in out
