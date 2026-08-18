"""Profile scope guardrails: `dag_id_prefix` and `allow_commands`.

Both are agent guardrails, not a security boundary — the point of these tests is
that a restricted profile fails *before* touching the server (so an out-of-scope
dag_id is never sent), and that the failure says why.
"""

from __future__ import annotations

import pytest

from conftest import BASE_URL, FakeResponse, paged
from datus_airflow_plugin.cli import build_parser, main
from datus_airflow_plugin.config import COMMAND_GROUPS, Settings
from datus_airflow_plugin.errors import ConfigError, UsageError


@pytest.fixture
def scoped(settings):
    """The shared settings fixture, limited to one dag_id prefix."""
    settings.dag_id_prefix = ("team_a_",)
    return settings


def _subparser_choices(parser) -> dict:
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


# ------------------------------------------------------------ config parsing


def test_scope_fields_default_to_unrestricted():
    settings = Settings.from_profile({"api_base_url": BASE_URL})
    assert settings.dag_id_prefix == ()
    assert settings.allow_commands == ()
    assert settings.scoped is False
    assert settings.allowed_groups() is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("team_a_", ("team_a_",)),
        ("team_a_,team_b_", ("team_a_", "team_b_")),
        (" team_a_ , team_b_ ", ("team_a_", "team_b_")),
        ("team_a_,,", ("team_a_",)),
        (["team_a_", "team_b_"], ("team_a_", "team_b_")),  # YAML list form
        ("", ()),
        (None, ()),
    ],
)
def test_dag_id_prefix_parsing(raw, expected):
    settings = Settings.from_profile({"api_base_url": BASE_URL, "dag_id_prefix": raw})
    assert settings.dag_id_prefix == expected


def test_allow_commands_parsing_and_allowed_groups():
    settings = Settings.from_profile(
        {"api_base_url": BASE_URL, "allow_commands": "dags, tasks ,version"}
    )
    assert settings.allow_commands == ("dags", "tasks", "version")
    assert settings.allowed_groups() == {"dags", "tasks", "version"}


def test_allow_commands_rejects_subcommand_level():
    with pytest.raises(ConfigError) as exc:
        Settings.from_profile({"api_base_url": BASE_URL, "allow_commands": "dags list"})
    message = str(exc.value)
    assert "top-level command groups" in message
    assert "'dags'" in message  # points at the group that would work


def test_allow_commands_rejects_unknown_group():
    with pytest.raises(ConfigError) as exc:
        Settings.from_profile({"api_base_url": BASE_URL, "allow_commands": "frobnicate"})
    assert "unknown command group" in str(exc.value)


# ------------------------------------------------------------ allow_commands


def test_build_parser_registers_only_allowed_groups():
    parser = build_parser({"dags", "version"})
    assert set(_subparser_choices(parser)) == {"dags", "version"}


def test_disabled_group_reports_a_policy_error(capsys):
    rc = main(["variables", "list"], {"api_base_url": BASE_URL, "allow_commands": "dags,version"})
    assert rc == 2
    err = capsys.readouterr().err
    assert "allow_commands" in err
    assert "'variables'" in err
    assert "dags, version" in err  # tells the agent what it may use instead


def test_allowed_group_passes_the_policy_check(capsys):
    # No api_base_url, so it fails later with a config error (3) — proving the
    # group itself was not blocked by the allowlist.
    rc = main(["dags", "list"], {"allow_commands": "dags"})
    assert rc == 3
    assert "api_base_url" in capsys.readouterr().err


def test_help_lists_only_allowed_groups(capsys):
    rc = main(["--help"], {"api_base_url": BASE_URL, "allow_commands": "dags,version"})
    assert rc == 0
    out = capsys.readouterr().out
    assert "dags" in out and "version" in out
    for hidden in ("variables", "connections", "pools", "backfill"):
        assert hidden not in out


def test_unknown_group_still_reports_a_usage_error():
    assert main(["frobnicate"], {"allow_commands": "dags"}) == 2


def test_config_error_is_reported_before_parsing(capsys):
    rc = main(["dags", "list"], {"api_base_url": BASE_URL, "allow_commands": "dags list"})
    assert rc == 3
    assert "top-level command groups" in capsys.readouterr().err


# ----------------------------------------------------------- dag_id_prefix


def test_in_scope_dag_id_is_allowed(run_cli, fake_session, scoped, capsys):
    fake_session.add(
        "GET", "/api/v2/dags/team_a_etl/details",
        FakeResponse(json_data={"dag_id": "team_a_etl"}),
    )
    assert run_cli(["dags", "details", "team_a_etl", "-o", "json"], scoped) == 0
    assert "team_a_etl" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["dags", "details", "other_etl"],
        ["dags", "trigger", "other_etl"],
        ["dags", "delete", "other_etl", "-y"],
        ["dags", "state", "other_etl", "run1"],
        ["dags", "clear-run", "other_etl", "run1", "-y"],
        ["dags", "show", "other_etl"],
        ["dags", "source", "other_etl"],
        ["dags", "next-execution", "other_etl"],
        ["dags", "list-runs", "other_etl"],
        ["tasks", "list", "other_etl"],
        ["tasks", "state", "other_etl", "run1", "t1"],
        ["tasks", "states-for-dag-run", "other_etl", "run1"],
        ["tasks", "clear", "other_etl", "-y"],
        ["tasks", "failed-deps", "other_etl", "run1", "t1"],
        ["tasks", "logs", "other_etl", "run1", "t1"],
        ["backfill", "create", "--dag-id", "other_etl", "--from-date", "2026-01-01", "--to-date", "2026-01-02"],
        ["backfill", "list", "--dag-id", "other_etl"],
    ],
)
def test_out_of_scope_dag_id_fails_before_any_request(run_cli, fake_session, scoped, argv):
    with pytest.raises(UsageError) as exc:
        run_cli(argv, scoped)
    assert exc.value.exit_code == 2
    assert "out of scope" in str(exc.value)
    assert "'team_a_'" in str(exc.value)
    assert fake_session.calls == [], "the server must never be contacted"


def test_pause_is_all_or_nothing(run_cli, fake_session, scoped):
    """A bulk pause with one bad dag_id must not half-apply."""
    fake_session.add(
        "PATCH", "/api/v2/dags/team_a_etl",
        FakeResponse(json_data={"dag_id": "team_a_etl", "is_paused": True}),
    )
    with pytest.raises(UsageError):
        run_cli(["dags", "pause", "team_a_etl", "other_etl"], scoped)
    assert fake_session.calls == []


def test_multiple_prefixes_accept_either(run_cli, fake_session, scoped):
    scoped.dag_id_prefix = ("team_a_", "shared_")
    for dag_id in ("team_a_etl", "shared_etl"):
        fake_session.add(
            "GET", f"/api/v2/dags/{dag_id}/details", FakeResponse(json_data={"dag_id": dag_id})
        )
        assert run_cli(["dags", "details", dag_id, "-o", "json"], scoped) == 0
    with pytest.raises(UsageError):
        run_cli(["dags", "details", "other_etl"], scoped)


def test_unscoped_profile_touches_nothing(run_cli, fake_session, settings, capsys):
    fake_session.add(
        "GET", "/api/v2/dags/anything/details", FakeResponse(json_data={"dag_id": "anything"})
    )
    assert run_cli(["dags", "details", "anything", "-o", "json"], settings) == 0


# ---------------------------------------------------------- list filtering


def test_dags_list_filters_rows_and_narrows_the_query(run_cli, fake_session, scoped, capsys):
    fake_session.add(
        "GET", "/api/v2/dags",
        FakeResponse(json_data=paged("dags", [
            {"dag_id": "team_a_etl", "is_paused": False},
            {"dag_id": "other_etl", "is_paused": False},
            {"dag_id": "x_team_a_etl", "is_paused": False},  # substring, not prefix
        ])),
    )
    assert run_cli(["dags", "list"], scoped) == 0
    captured = capsys.readouterr()
    assert "team_a_etl" in captured.out
    assert "other_etl" not in captured.out
    assert "x_team_a_etl" not in captured.out, "dag_id_pattern is a substring match; the client must enforce the prefix"
    assert "2 row(s) outside dag_id_prefix" in captured.err
    # the prefix is also pushed to the server to shrink the transfer
    assert fake_session.calls_to("GET", "/api/v2/dags")[0]["params"]["dag_id_pattern"] == "team_a_"


def test_explicit_pattern_is_not_overridden(run_cli, fake_session, scoped):
    fake_session.add("GET", "/api/v2/dags", FakeResponse(json_data=paged("dags", [])))
    assert run_cli(["dags", "list", "--pattern", "%etl%"], scoped) == 0
    assert fake_session.calls_to("GET", "/api/v2/dags")[0]["params"]["dag_id_pattern"] == "%etl%"


def test_multiple_prefixes_skip_the_server_pattern(run_cli, fake_session, scoped):
    scoped.dag_id_prefix = ("team_a_", "shared_")
    fake_session.add("GET", "/api/v2/dags", FakeResponse(json_data=paged("dags", [])))
    assert run_cli(["dags", "list"], scoped) == 0
    params = fake_session.calls_to("GET", "/api/v2/dags")[0]["params"]
    assert "dag_id_pattern" not in params


def test_list_runs_across_all_dags_is_filtered(run_cli, fake_session, scoped, capsys):
    fake_session.add(
        "GET", "/api/v2/dags/~/dagRuns",
        FakeResponse(json_data=paged("dag_runs", [
            {"dag_id": "team_a_etl", "dag_run_id": "r1", "state": "success"},
            {"dag_id": "other_etl", "dag_run_id": "r2", "state": "failed"},
        ])),
    )
    assert run_cli(["dags", "list-runs"], scoped) == 0
    captured = capsys.readouterr()
    assert "r1" in captured.out and "r2" not in captured.out
    assert "1 row(s) outside dag_id_prefix" in captured.err


def test_asset_events_are_filtered_by_source_dag(run_cli, fake_session, scoped, capsys):
    fake_session.add(
        "GET", "/api/v2/assets/events",
        FakeResponse(json_data=paged("asset_events", [
            {"asset_id": 1, "source_dag_id": "team_a_etl"},
            {"asset_id": 2, "source_dag_id": "other_etl"},
        ])),
    )
    assert run_cli(["assets", "events"], scoped) == 0
    captured = capsys.readouterr()
    assert "team_a_etl" in captured.out and "other_etl" not in captured.out


def test_import_errors_warn_that_they_are_unfiltered(run_cli, fake_session, scoped, capsys):
    fake_session.add(
        "GET", "/api/v2/importErrors",
        FakeResponse(json_data=paged("import_errors", [
            {"filename": "/dags/other.py", "stack_trace": "boom"},
        ])),
    )
    assert run_cli(["dags", "list-import-errors"], scoped) == 0
    assert "not filtered by dag_id_prefix" in capsys.readouterr().err


def test_v1_list_filtering_works_the_same(run_cli, fake_session, scoped, capsys):
    scoped.api_version = "v1"
    fake_session.add(
        "GET", "/api/v1/dags",
        FakeResponse(json_data=paged("dags", [
            {"dag_id": "team_a_etl", "is_paused": False},
            {"dag_id": "other_etl", "is_paused": False},
        ])),
    )
    assert run_cli(["dags", "list"], scoped) == 0
    captured = capsys.readouterr()
    assert "team_a_etl" in captured.out and "other_etl" not in captured.out
    assert fake_session.calls_to("GET", "/api/v1/dags")[0]["params"]["dag_id_pattern"] == "team_a_"


def test_v1_out_of_scope_dag_id_still_fails(run_cli, fake_session, scoped):
    scoped.api_version = "v1"
    with pytest.raises(UsageError):
        run_cli(["dags", "trigger", "other_etl"], scoped)
    assert fake_session.calls == []


# ------------------------------------------------- commands without a dag_id


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["assets", "materialize", "--name", "sales"], "assets materialize"),
        (["backfill", "pause", "7"], "backfill pause"),
        (["backfill", "unpause", "7"], "backfill unpause"),
        (["backfill", "cancel", "7"], "backfill cancel"),
    ],
)
def test_commands_without_a_dag_id_are_refused_when_scoped(
    run_cli, fake_session, scoped, argv, expected
):
    with pytest.raises(UsageError) as exc:
        run_cli(argv, scoped)
    message = str(exc.value)
    assert expected in message
    assert "dag_id_prefix" in message
    assert fake_session.calls == []


def test_those_commands_still_work_without_a_prefix(run_cli, fake_session, settings, capsys):
    fake_session.add(
        "PUT", "/api/v2/backfills/7/cancel", FakeResponse(json_data={"id": 7, "dag_id": "any"})
    )
    assert run_cli(["backfill", "cancel", "7", "-o", "json"], settings) == 0
