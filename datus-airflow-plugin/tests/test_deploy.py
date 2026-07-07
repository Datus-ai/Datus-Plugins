"""Deployment: file collection, local/S3 targets, prune, verify polling."""

from __future__ import annotations

import pytest

from datus_airflow_plugin.client import AirflowClient
from datus_airflow_plugin.config import Settings
from datus_airflow_plugin.deploy import (
    LocalTarget,
    S3Target,
    capture_import_error_state,
    capture_parse_state,
    collect_files,
    parse_s3_uri,
    verify_dags,
)
from datus_airflow_plugin.errors import PluginError, UsageError

from conftest import BASE_URL, FakeResponse, paged


# ------------------------------------------------------------ collection


def test_collect_files_filters_directories(tmp_path):
    dags = tmp_path / "dags"
    (dags / "team" / "__pycache__").mkdir(parents=True)
    (dags / ".hidden").mkdir()
    (dags / "etl.py").write_text("dag")
    (dags / "bundle.zip").write_bytes(b"zip")
    (dags / "notes.txt").write_text("skip me")
    (dags / "team" / "sub.py").write_text("dag")
    (dags / "team" / "__pycache__" / "x.pyc").write_bytes(b"")
    (dags / ".hidden" / "secret.py").write_text("dag")

    items = collect_files([str(dags)])
    assert [i.rel for i in items] == ["bundle.zip", "etl.py", "team/sub.py"]

    items = collect_files([str(dags)], all_files=True)
    assert "notes.txt" in [i.rel for i in items]
    assert not any("pycache" in i.rel or i.rel.startswith(".hidden") for i in items)


def test_collect_files_single_file_and_missing(tmp_path):
    f = tmp_path / "one.py"
    f.write_text("dag")
    assert [i.rel for i in collect_files([str(f)])] == ["one.py"]
    with pytest.raises(UsageError):
        collect_files([str(tmp_path / "absent.py")])


def test_collect_files_conflicting_rel_paths(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "dag.py").write_text("1")
    (tmp_path / "b" / "dag.py").write_text("2")
    with pytest.raises(UsageError):
        collect_files([str(tmp_path / "a" / "dag.py"), str(tmp_path / "b" / "dag.py")])


# ---------------------------------------------------------------- targets


def test_parse_s3_uri():
    assert parse_s3_uri("s3://bucket/dags") == ("bucket", "dags/")
    assert parse_s3_uri("s3://bucket") == ("bucket", "")
    with pytest.raises(UsageError):
        parse_s3_uri("s3://")


def test_local_target_upload_list_delete(tmp_path):
    src = tmp_path / "src"
    (src / "team").mkdir(parents=True)
    (src / "etl.py").write_text("dag-a")
    (src / "team" / "sub.py").write_text("dag-b")
    dest = tmp_path / "dags"

    target = LocalTarget(str(dest))
    target.upload(collect_files([str(src)]), log=lambda m: None)
    assert (dest / "etl.py").read_text() == "dag-a"
    assert (dest / "team" / "sub.py").read_text() == "dag-b"
    assert target.list_keys() == {"etl.py", "team/sub.py"}

    target.delete(["team/sub.py"], log=lambda m: None)
    assert not (dest / "team").exists()  # emptied directories are removed
    assert target.list_keys() == {"etl.py"}


class FakeS3Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload_file(self, filename, bucket, key):
        with open(filename, "rb") as fh:
            self.objects[key] = fh.read()

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(client.objects) if k.startswith(Prefix)]}

        return Paginator()

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)


def test_s3_client_assumes_iam_role_when_role_arn_configured(monkeypatch):
    sessions = []

    class FakeSTS:
        def assume_role(self, **kwargs):
            sessions.append(("assume_role", kwargs))
            return {"Credentials": {
                "AccessKeyId": "AKIATEMP", "SecretAccessKey": "temp-secret", "SessionToken": "temp-token",
            }}

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.region_name = kwargs.get("region_name") or "us-east-1"
            sessions.append(("session", kwargs))

        def client(self, name, **kwargs):
            if name == "sts":
                return FakeSTS()
            sessions.append(("client", name, kwargs))
            return f"s3-client-{self.kwargs.get('aws_access_key_id')}"

    import boto3

    monkeypatch.setattr(boto3.session, "Session", FakeSession)
    settings = Settings.from_profile({
        "api_base_url": BASE_URL,
        "s3": {
            "profile": "bootstrap",
            "region": "eu-west-1",
            "role_arn": "arn:aws:iam::123456789012:role/dags-deployer",
            "external_id": "team-a",
        },
    })
    client = S3Target._build_client(settings)

    assert client == "s3-client-AKIATEMP"  # built from the assumed-role credentials
    _, base_kwargs = sessions[0]
    assert base_kwargs == {"profile_name": "bootstrap", "region_name": "eu-west-1"}
    _, assume_kwargs = sessions[1]
    assert assume_kwargs == {
        "RoleArn": "arn:aws:iam::123456789012:role/dags-deployer",
        "RoleSessionName": "datus-airflow-plugin",
        "ExternalId": "team-a",
    }
    _, temp_kwargs = sessions[2]
    assert temp_kwargs["aws_session_token"] == "temp-token"
    assert temp_kwargs["region_name"] == "eu-west-1"


def test_s3_target_upload_list_delete(tmp_path):
    (tmp_path / "etl.py").write_text("dag")
    fake = FakeS3Client()
    fake.objects["dags/stale.py"] = b"old"
    target = S3Target("s3://bucket/dags/", Settings(), client=fake)

    target.upload(collect_files([str(tmp_path / "etl.py")]), log=lambda m: None)
    assert fake.objects["dags/etl.py"] == b"dag"
    assert target.list_keys() == {"etl.py", "stale.py"}

    target.delete(["stale.py"], log=lambda m: None)
    assert "dags/stale.py" not in fake.objects


# ----------------------------------------------------------------- verify


def make_client(fake_session, tmp_path):
    settings = Settings.from_profile(
        {"api_base_url": BASE_URL, "token": "t", "cache_dir": str(tmp_path / "cache")}
    )
    return AirflowClient(settings, session=fake_session)


def test_verify_succeeds_when_parse_marker_changes(fake_session, tmp_path):
    client = make_client(fake_session, tmp_path)
    fake_session.add("GET", "/api/v2/importErrors", FakeResponse(json_data=paged("import_errors", [])))
    fake_session.add(
        "GET",
        "/api/v2/dags/etl",
        [
            FakeResponse(json_data={"dag_id": "etl", "last_parsed_time": "T1"}),  # pre-capture
            FakeResponse(json_data={"dag_id": "etl", "last_parsed_time": "T1"}),  # not yet re-parsed
            FakeResponse(json_data={"dag_id": "etl", "last_parsed_time": "T2"}),  # re-parsed
        ],
    )
    pre = capture_parse_state(client, ["etl"])
    assert pre == {"etl": "T1"}
    verify_dags(
        client, ["etl"], pre, ["etl.py"], {}, timeout=60, interval=0, log=lambda m: None,
        sleep=lambda s: None,
    )


def test_verify_fails_fast_on_new_import_error(fake_session, tmp_path):
    client = make_client(fake_session, tmp_path)
    fake_session.add(
        "GET",
        "/api/v2/importErrors",
        FakeResponse(json_data=paged("import_errors", [
            {"import_error_id": 5, "filename": "/dags/etl.py",
             "timestamp": "T9", "stack_trace": "SyntaxError: boom"},
        ])),
    )
    with pytest.raises(PluginError) as exc:
        verify_dags(
            client, ["etl"], {"etl": None}, ["etl.py"], {}, timeout=60, interval=0,
            log=lambda m: None, sleep=lambda s: None,
        )
    assert "SyntaxError: boom" in str(exc.value)


def test_verify_ignores_preexisting_unchanged_import_error(fake_session, tmp_path):
    client = make_client(fake_session, tmp_path)
    error = {"import_error_id": 5, "filename": "/dags/other.py", "timestamp": "T1", "stack_trace": "old"}
    fake_session.add("GET", "/api/v2/importErrors", FakeResponse(json_data=paged("import_errors", [error])))
    fake_session.add(
        "GET", "/api/v2/dags/etl", FakeResponse(json_data={"dag_id": "etl", "last_parsed_time": "T2"})
    )
    pre_errors = capture_import_error_state(client)
    # other.py's old error must not fail a deploy that ships other.py again unchanged
    verify_dags(
        client, ["etl"], {"etl": "T1"}, ["other.py"], pre_errors, timeout=60, interval=0,
        log=lambda m: None, sleep=lambda s: None,
    )


def test_verify_times_out(fake_session, tmp_path):
    client = make_client(fake_session, tmp_path)
    fake_session.add("GET", "/api/v2/importErrors", FakeResponse(json_data=paged("import_errors", [])))
    fake_session.add(
        "GET", "/api/v2/dags/etl", FakeResponse(404, json_data={"detail": "not found"})
    )
    with pytest.raises(PluginError) as exc:
        verify_dags(
            client, ["etl"], {"etl": None}, ["etl.py"], {}, timeout=0, interval=0,
            log=lambda m: None, sleep=lambda s: None,
        )
    assert "timed out" in str(exc.value)


# ------------------------------------------------------- CLI integration


def test_deploy_cli_to_local_folder_with_prune_and_dry_run(run_cli, fake_session, tmp_path, capsys, settings):
    src = tmp_path / "src"
    src.mkdir()
    (src / "etl.py").write_text("dag")
    dest = tmp_path / "dags"
    dest.mkdir()
    (dest / "stale.py").write_text("old")

    settings.dags_folder = str(dest)

    rc = run_cli(["dags", "deploy", str(src), "--prune", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run] would upload" in out and "would delete" in out
    assert (dest / "stale.py").exists()  # dry run changed nothing
    assert not (dest / "etl.py").exists()

    rc = run_cli(["dags", "deploy", str(src), "--prune", "-y"])
    assert rc == 0
    assert (dest / "etl.py").read_text() == "dag"
    assert not (dest / "stale.py").exists()


def test_deploy_cli_requires_a_destination(run_cli, tmp_path, settings):
    src = tmp_path / "etl.py"
    src.write_text("dag")
    settings.dags_folder = None
    with pytest.raises(PluginError) as exc:
        run_cli(["dags", "deploy", str(src)])
    assert exc.value.exit_code == 3


def test_undeploy_cli_deletes_only_named_files(run_cli, tmp_path, capsys, settings):
    dest = tmp_path / "dags"
    (dest / "team_a").mkdir(parents=True)
    (dest / "team_a" / "old.py").write_text("old")
    (dest / "keep.py").write_text("keep")
    settings.dags_folder = str(dest)

    rc = run_cli(["dags", "undeploy", "team_a/old.py", "--dry-run"])
    assert rc == 0
    assert "[dry-run] would delete" in capsys.readouterr().out
    assert (dest / "team_a" / "old.py").exists()  # dry run changed nothing

    rc = run_cli(["dags", "undeploy", "team_a/old.py", "-y"])
    assert rc == 0
    assert not (dest / "team_a").exists()  # file gone, emptied dir cleaned up
    assert (dest / "keep.py").exists()
    assert "dags delete" in capsys.readouterr().out  # metadata cleanup hint


def test_undeploy_cli_fails_before_deleting_when_a_path_is_missing(run_cli, tmp_path, settings):
    dest = tmp_path / "dags"
    dest.mkdir()
    (dest / "real.py").write_text("dag")
    settings.dags_folder = str(dest)
    with pytest.raises(UsageError) as exc:
        run_cli(["dags", "undeploy", "real.py", "typo.py", "-y"])
    assert "typo.py" in str(exc.value)
    assert (dest / "real.py").exists()  # nothing was deleted


def test_undeploy_cli_rejects_path_traversal_and_absolute(run_cli, tmp_path, settings):
    settings.dags_folder = str(tmp_path)
    for bad in ("../outside.py", "/etc/passwd", "a/../../b.py"):
        with pytest.raises(UsageError):
            run_cli(["dags", "undeploy", bad, "-y"])


def test_undeploy_cli_requires_confirmation(run_cli, tmp_path, settings):
    dest = tmp_path / "dags"
    dest.mkdir()
    (dest / "x.py").write_text("dag")
    settings.dags_folder = str(dest)
    with pytest.raises(UsageError):  # stdin is not a tty in tests
        run_cli(["dags", "undeploy", "x.py"])
    assert (dest / "x.py").exists()


def test_deploy_cli_prefix_prunes_only_within_prefix(run_cli, fake_session, tmp_path, settings):
    src = tmp_path / "src"
    src.mkdir()
    (src / "etl.py").write_text("dag")
    dest = tmp_path / "dags"
    (dest / "team_b").mkdir(parents=True)
    (dest / "team_b" / "other.py").write_text("keep")
    (dest / "team_a").mkdir()
    (dest / "team_a" / "stale.py").write_text("old")

    settings.dags_folder = str(dest)
    rc = run_cli(["dags", "deploy", str(src), "--prefix", "team_a", "--prune", "-y"])
    assert rc == 0
    assert (dest / "team_a" / "etl.py").exists()
    assert not (dest / "team_a" / "stale.py").exists()
    assert (dest / "team_b" / "other.py").exists()  # outside the prefix: untouched
