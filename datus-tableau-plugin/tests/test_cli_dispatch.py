"""CLI contracts using a strict local substitute for the remote Tableau service."""

from __future__ import annotations

import json

import pytest

from datus_tableau_plugin import cli
from datus_tableau_plugin.config import Settings
from test_sync import Client, workbook

PROFILE = {
    "server_url": "https://tableau.example.test",
    "site_content_url": "",
    "auth_mode": "token",
    "token_name": "synthetic-unused",
    "token_secret": "synthetic-unused",
}
BOOKS = [
    {"id": "book1", "name": "First", "project_id": "sales"},
    {"id": "book2", "name": "Second", "project_id": "finance"},
]


class InventoryClient:
    __slots__ = ("calls", "invalid_workbook", "interrupt")

    def __init__(self, *, invalid_workbook=False, interrupt=False):
        self.calls = []
        self.invalid_workbook = invalid_workbook
        self.interrupt = interrupt

    def __enter__(self):
        self.calls.append("enter")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.calls.append(("exit", exc_type))

    def status(self):
        self.calls.append("status")
        if self.interrupt:
            raise KeyboardInterrupt
        return {"status": "ok", "api_version": "3.21"}

    def list_resources(self, group, *, project_id=None):
        self.calls.append(("list", group, project_id))
        assert group in {"projects", "workbooks"}
        if group == "workbooks":
            return BOOKS
        return [{"id": "project1", "name": "Sales"}]

    def get_resource(self, group, identifier):
        self.calls.append(("get", group, identifier))
        assert group == "flows"
        return {"id": identifier, "name": "Synthetic flow", "connections": []}

    def download(self, group, identifier):
        self.calls.append(("download", group, identifier))
        assert group == "workbooks"
        assert identifier in {"book1", "book2"}
        if self.invalid_workbook:
            return b"<invalid"
        return workbook(identifier)


def use_client(monkeypatch, client):
    def construct(settings):
        assert isinstance(settings, Settings)
        assert settings.server_url == "https://tableau.example.test"
        monkeypatch.setattr(
            cli, "TableauClient", lambda unused: pytest.fail("constructed twice")
        )
        return client

    monkeypatch.setattr(cli, "TableauClient", construct)


@pytest.mark.parametrize(
    "value,expected",
    [
        ([], "(no results)"),
        ([1, "two"], '[1, "two"]'),
        ({"id": "a", "details": {"x": 1}}, 'id  details \na   {"x": 1}'),
    ],
)
def test_table_output_preserves_empty_scalar_and_nested_structures(value, expected):
    assert cli.render(value, "table") == expected


@pytest.mark.parametrize(
    "argv,expected,expected_call",
    [
        (
            ["projects", "list"],
            [{"id": "project1", "name": "Sales"}],
            ("list", "projects", None),
        ),
        (
            ["workbooks", "list", "--project-id", "finance"],
            BOOKS,
            ("list", "workbooks", "finance"),
        ),
        (
            ["flows", "get", "flow-7"],
            {"id": "flow-7", "name": "Synthetic flow", "connections": []},
            ("get", "flows", "flow-7"),
        ),
    ],
)
def test_resource_commands_dispatch_exact_arguments_and_json(
    argv, expected, expected_call, monkeypatch, capsys
):
    client = InventoryClient()
    use_client(monkeypatch, client)
    assert cli.main(argv, PROFILE) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == expected
    assert captured.err == ""
    assert client.calls == ["enter", expected_call, ("exit", None)]


def test_status_json_contract_and_context_cleanup(monkeypatch, capsys):
    client = InventoryClient()
    use_client(monkeypatch, client)
    assert cli.main(["status"], PROFILE) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "ok", "api_version": "3.21"}
    assert captured.err == ""
    assert client.calls == ["enter", "status", ("exit", None)]


def test_dashboard_list_filters_before_real_native_download_and_parse(
    monkeypatch, capsys
):
    client = InventoryClient()
    use_client(monkeypatch, client)
    assert cli.main(["dashboards", "list", "--project-id", "sales"], PROFILE) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [
        {
            "id": "dashboard:book1:Overview",
            "name": "Overview",
            "workbook_id": "book1",
            "project_id": "sales",
            "status": "ok",
        },
        {
            "id": "dashboard:book1:Other",
            "name": "Other",
            "workbook_id": "book1",
            "project_id": "sales",
            "status": "ok",
        },
    ]
    assert captured.err == ""
    assert client.calls == [
        "enter",
        ("list", "workbooks", None),
        ("download", "workbooks", "book1"),
        ("exit", None),
    ]


def test_scoped_dashboard_get_returns_native_asset_from_only_selected_workbook(
    monkeypatch, capsys
):
    client = InventoryClient()
    use_client(monkeypatch, client)
    assert cli.main(["dashboards", "get", "dashboard:book2:Overview"], PROFILE) == 0
    captured = capsys.readouterr()
    asset = json.loads(captured.out)
    assert set(asset) == {"id", "kind", "name", "source", "definition", "status"}
    assert (asset["id"], asset["kind"], asset["name"], asset["status"]) == (
        "dashboard:book2:Overview",
        "dashboard",
        "Overview",
        "ok",
    )
    assert asset["source"] == {"workbook_luid": "book2", "native_name": "Overview"}
    assert asset["definition"]["worksheets"] == ["chart:book2:Revenue"]
    assert captured.err == ""
    assert client.calls == [
        "enter",
        ("list", "workbooks", None),
        ("download", "workbooks", "book2"),
        ("exit", None),
    ]


@pytest.mark.parametrize(
    "identifier,message,discovery_calls",
    [
        (
            "book1:Overview",
            "use scoped dashboard identifiers returned by dashboards list",
            [],
        ),
        (
            "dashboard:book1:Missing",
            "dashboard ID was not found; use dashboards list to discover scoped identifiers",
            [("list", "workbooks", None), ("download", "workbooks", "book1")],
        ),
    ],
)
def test_invalid_or_missing_dashboard_is_usage_error_without_success_output(
    identifier, message, discovery_calls, monkeypatch, capsys
):
    from datus_tableau_plugin.errors import UsageError

    client = InventoryClient()
    use_client(monkeypatch, client)
    assert cli.main(["dashboards", "get", identifier], PROFILE) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {message}\n"
    assert client.calls == ["enter", *discovery_calls, ("exit", UsageError)]


def test_failed_workbook_definition_prevents_partial_dashboard_inventory_success(
    monkeypatch, capsys
):
    from datus_tableau_plugin.errors import PluginError

    client = InventoryClient(invalid_workbook=True)
    use_client(monkeypatch, client)
    assert cli.main(["dashboards", "list", "--project-id", "sales"], PROFILE) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "error: dashboard inventory is incomplete: one or more workbook definitions could not be downloaded or parsed\n"
    )
    assert client.calls == [
        "enter",
        ("list", "workbooks", None),
        ("download", "workbooks", "book1"),
        ("exit", PluginError),
    ]


def test_keyboard_interrupt_has_stable_exit_code_and_closes_client(monkeypatch, capsys):
    client = InventoryClient(interrupt=True)
    use_client(monkeypatch, client)
    assert cli.main(["status"], PROFILE) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "interrupted\n"
    assert client.calls == ["enter", "status", ("exit", KeyboardInterrupt)]


def test_sync_partial_result_remains_nonzero_and_written_bundle_is_inspectable(
    tmp_path, monkeypatch, capsys
):
    class LocalSyncClient(Client):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.chdir(tmp_path)
    client = LocalSyncClient(physical=True)
    monkeypatch.setattr(cli, "TableauClient", lambda settings: client)
    assert (
        cli.main(["sync", "run", "--dashboard-id", "dashboard:book1:Overview"], PROFILE)
        == 1
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "partial"
    assert result["summary"]["dashboards"] == 1
    assert captured.err == ""
    monkeypatch.setattr(
        cli, "TableauClient", lambda settings: pytest.fail("offline inspect connected")
    )
    assert cli.main(["sync", "inspect", result["output_dir"]], {}) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["manifest"]["status"] == "partial"
    assert [item["code"] for item in inspected["coverage"]] == [
        "external_etl_unresolved",
        "selection_scoped_source_copy",
    ]
