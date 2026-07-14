"""Command tests for datus s3."""

from __future__ import annotations

import io


def test_ls_objects(run_cli, s3, capsys):
    s3.set(
        "list_objects_v2",
        {"Contents": [{"Key": "data/a.csv", "Size": 12}], "CommonPrefixes": [{"Prefix": "data/sub/"}]},
    )
    assert run_cli(["ls", "s3://bucket/data/"]) == 0
    out = capsys.readouterr().out
    assert "data/a.csv" in out and "data/sub/" in out
    assert s3.calls[0]["kwargs"]["Delimiter"] == "/"


def test_ls_no_uri_lists_buckets(run_cli, s3, capsys):
    s3.set("list_buckets", {"Buckets": [{"Name": "my-bucket"}]})
    assert run_cli(["ls"]) == 0
    assert "my-bucket" in capsys.readouterr().out


def test_stat(run_cli, s3, capsys):
    s3.set("head_object", {"ContentLength": 12, "ContentType": "text/csv", "ResponseMetadata": {}})
    assert run_cli(["stat", "s3://b/a.csv"]) == 0
    assert "ContentLength" in capsys.readouterr().out


def test_cat(run_cli, s3, capsys):
    s3.set("get_object", {"Body": io.BytesIO(b"col1,col2\n1,2\n")})
    assert run_cli(["cat", "s3://b/a.csv"]) == 0
    assert "col1,col2" in capsys.readouterr().out


def test_presign(run_cli, s3, capsys):
    s3.set("generate_presigned_url", "https://example.com/signed")
    assert run_cli(["presign", "s3://b/a.csv", "--expires", "60"]) == 0
    assert "https://example.com/signed" in capsys.readouterr().out
    kwargs = s3.calls_to("generate_presigned_url")[0]["kwargs"]
    assert kwargs["ClientMethod"] == "get_object" and kwargs["ExpiresIn"] == 60


def test_select_streams_records(run_cli, s3, capsys):
    s3.set(
        "select_object_content",
        {"Payload": [{"Records": {"Payload": b'{"n":5}'}}, {"Stats": {}}, {"End": {}}]},
    )
    assert run_cli(["select", "s3://b/a.csv", "--format", "csv", "--sql", "select * from s3object"]) == 0
    assert '{"n":5}' in capsys.readouterr().out
    kwargs = s3.calls_to("select_object_content")[0]["kwargs"]
    assert kwargs["Expression"] == "select * from s3object"
    assert kwargs["InputSerialization"] == {"CSV": {"FileHeaderInfo": "NONE"}}


def test_cp_upload_local_to_s3(run_cli, s3, tmp_path, capsys):
    f = tmp_path / "hello.txt"
    f.write_text("hi")
    assert run_cli(["cp", str(f), "s3://b/prefix/"]) == 0
    kwargs = s3.calls_to("put_object")[0]["kwargs"]
    assert kwargs["Bucket"] == "b" and kwargs["Key"] == "prefix/hello.txt"
    assert kwargs["Body"] == b"hi"


def test_cp_upload_applies_kms(run_cli, s3, tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hi")
    profile = {"region": "us-east-1", "kms_key_id": "arn:aws:kms:::key/abc"}
    assert run_cli(["cp", str(f), "s3://b/k.txt"], profile) == 0
    kwargs = s3.calls_to("put_object")[0]["kwargs"]
    assert kwargs["ServerSideEncryption"] == "aws:kms"
    assert kwargs["SSEKMSKeyId"] == "arn:aws:kms:::key/abc"


def test_cp_download_s3_to_local(run_cli, s3, tmp_path):
    s3.set("get_object", {"Body": io.BytesIO(b"payload")})
    dest = tmp_path / "out.txt"
    assert run_cli(["cp", "s3://b/a.txt", str(dest)]) == 0
    assert dest.read_bytes() == b"payload"


def test_rm_requires_confirmation_then_deletes(run_cli, s3):
    assert run_cli(["rm", "s3://b/a.txt"]) == 2  # UsageError: no tty, no -y
    assert run_cli(["rm", "s3://b/a.txt", "-y"]) == 0
    assert s3.calls_to("delete_object")[0]["kwargs"]["Key"] == "a.txt"


def test_rm_recursive_batches(run_cli, s3, capsys):
    s3.set("list_objects_v2", {"Contents": [{"Key": "p/1"}, {"Key": "p/2"}]})
    assert run_cli(["rm", "s3://b/p/", "-r", "-y"]) == 0
    delete = s3.calls_to("delete_objects")[0]["kwargs"]
    assert {o["Key"] for o in delete["Delete"]["Objects"]} == {"p/1", "p/2"}


def test_buckets_location(run_cli, s3, capsys):
    s3.set("get_bucket_location", {"LocationConstraint": "eu-west-1"})
    assert run_cli(["buckets", "location", "my-bucket"]) == 0
    assert "eu-west-1" in capsys.readouterr().out
