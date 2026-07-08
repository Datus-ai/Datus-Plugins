"""Command tests for datus quicksight (full capability)."""

from __future__ import annotations

ACCT = {"region": "us-east-1", "aws_account_id": "123456789012"}


def test_datasets_list_passes_account(run_cli, clients, capsys):
    clients["quicksight"].set_pages(
        "list_data_sets",
        [{"DataSetSummaries": [{"DataSetId": "d1", "Name": "sales", "ImportMode": "SPICE"}]}],
    )
    assert run_cli(["datasets", "list"], ACCT) == 0
    assert "d1" in capsys.readouterr().out
    assert clients["quicksight"].calls[0]["kwargs"]["AwsAccountId"] == "123456789012"


def test_missing_account_is_config_error(run_cli, clients):
    assert run_cli(["datasets", "list"], {"region": "us-east-1"}) == 3


def test_datasets_create_from_json(run_cli, clients, capsys):
    clients["quicksight"].set("create_data_set", {"DataSetId": "new1"})
    body = '{"DataSetId":"new1","Name":"n","PhysicalTableMap":{},"ImportMode":"SPICE"}'
    assert run_cli(["datasets", "create", "--cli-input", body], ACCT) == 0
    kwargs = clients["quicksight"].calls_to("create_data_set")[0]["kwargs"]
    assert kwargs["DataSetId"] == "new1" and kwargs["AwsAccountId"] == "123456789012"


def test_datasets_delete_confirmation(run_cli, clients):
    assert run_cli(["datasets", "delete", "d1"], ACCT) == 2  # no tty, no -y
    assert run_cli(["datasets", "delete", "d1", "-y"], ACCT) == 0
    assert clients["quicksight"].calls_to("delete_data_set")[0]["kwargs"]["DataSetId"] == "d1"


def test_refresh_run_wait(run_cli, clients, capsys):
    clients["quicksight"].set("create_ingestion", {"IngestionId": "i1", "IngestionStatus": "INITIALIZED"})
    clients["quicksight"].set(
        "describe_ingestion",
        [
            {"Ingestion": {"IngestionStatus": "RUNNING"}},
            {"Ingestion": {"IngestionStatus": "COMPLETED"}},
        ],
    )
    rc = run_cli(["refresh", "run", "d1", "--ingestion-id", "i1", "--wait", "--interval", "0"], ACCT)
    assert rc == 0
    assert "COMPLETED" in capsys.readouterr().out


def test_refresh_run_wait_failed_returns_1(run_cli, clients):
    clients["quicksight"].set("create_ingestion", {"IngestionId": "i2", "IngestionStatus": "INITIALIZED"})
    clients["quicksight"].set("describe_ingestion", {"Ingestion": {"IngestionStatus": "FAILED", "ErrorInfo": {"Type": "X"}}})
    assert run_cli(["refresh", "run", "d1", "--ingestion-id", "i2", "--wait", "--interval", "0"], ACCT) == 1


def test_dashboards_publish(run_cli, clients):
    assert run_cli(["dashboards", "publish", "dash1", "3"], ACCT) == 0
    kwargs = clients["quicksight"].calls_to("update_dashboard_published_version")[0]["kwargs"]
    assert kwargs["DashboardId"] == "dash1" and kwargs["VersionNumber"] == 3


def test_dashboards_embed_url(run_cli, clients, capsys):
    clients["quicksight"].set("generate_embed_url_for_registered_user", {"EmbedUrl": "https://embed"})
    rc = run_cli(["dashboards", "embed-url", "dash1", "--user-arn", "arn:aws:quicksight:::user/u"], ACCT)
    assert rc == 0
    assert "https://embed" in capsys.readouterr().out


def test_dashboards_embed_url_anonymous_default_arn(run_cli, clients, capsys):
    clients["quicksight"].set("generate_embed_url_for_anonymous_user", {"EmbedUrl": "https://anon"})
    rc = run_cli(["dashboards", "embed-url-anonymous", "dash1"], ACCT)
    assert rc == 0
    assert "https://anon" in capsys.readouterr().out
    kwargs = clients["quicksight"].calls_to("generate_embed_url_for_anonymous_user")[0]["kwargs"]
    assert kwargs["AuthorizedResourceArns"] == ["arn:aws:quicksight:*:123456789012:dashboard/dash1"]


def test_users_list_uses_identity_client(run_cli, clients, capsys):
    clients["quicksight-identity"].set_pages("list_users", [{"UserList": [{"UserName": "alice", "Role": "AUTHOR"}]}])
    assert run_cli(["users", "list"], ACCT) == 0
    assert "alice" in capsys.readouterr().out
    # regional client must NOT have been used for the identity op
    assert not clients["quicksight"].calls_to("list_users")
    assert clients["quicksight-identity"].calls_to("list_users")


def test_groups_member_add(run_cli, clients):
    assert run_cli(["groups", "member-add", "analysts", "alice"], ACCT) == 0
    kwargs = clients["quicksight-identity"].calls_to("create_group_membership")[0]["kwargs"]
    assert kwargs["GroupName"] == "analysts" and kwargs["MemberName"] == "alice"


def test_folders_member_add(run_cli, clients):
    assert run_cli(["folders", "member-add", "f1", "dash1", "--member-type", "DASHBOARD"], ACCT) == 0
    kwargs = clients["quicksight"].calls_to("create_folder_membership")[0]["kwargs"]
    assert kwargs["MemberType"] == "DASHBOARD" and kwargs["MemberId"] == "dash1"


def test_account_subscription(run_cli, clients, capsys):
    clients["quicksight"].set("describe_account_subscription", {"AccountInfo": {"Edition": "ENTERPRISE"}})
    assert run_cli(["account", "subscription"], ACCT) == 0
    assert "ENTERPRISE" in capsys.readouterr().out


def test_assets_import_from_json(run_cli, clients, capsys):
    clients["quicksight"].set("start_asset_bundle_import_job", {"AssetBundleImportJobId": "imp1"})
    body = '{"AssetBundleImportJobId":"imp1","AssetBundleImportSource":{"Body":"..."}}'
    assert run_cli(["assets", "import", "--cli-input", body], ACCT) == 0
    assert "imp1" in capsys.readouterr().out
