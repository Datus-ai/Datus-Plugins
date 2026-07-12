"""The Datus plugin contract: constructor, run_cli, skills_dir, system_prompt, cli_permissions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from datus_airflow_plugin.plugin import AirflowPlugin

PKG_DIR = Path(__file__).resolve().parents[1] / "datus_airflow_plugin"


def test_constructor_accepts_profile_keyword():
    plugin = AirflowPlugin(profile={"name": "prod", "api_base_url": "http://x"})
    assert plugin.profile["name"] == "prod"
    assert AirflowPlugin().profile == {}


def test_run_cli_help_returns_zero(capsys):
    rc = AirflowPlugin(profile={}).run_cli(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for group in ("dags", "tasks", "variables", "connections", "pools", "backfill"):
        assert group in out


def test_run_cli_unknown_command_returns_usage_error(capsys):
    rc = AirflowPlugin(profile={}).run_cli(["frobnicate"])
    assert rc == 2


def test_run_cli_without_base_url_is_config_error(capsys):
    rc = AirflowPlugin(profile={}).run_cli(["dags", "list"])
    assert rc == 3
    assert "api_base_url" in capsys.readouterr().err


def test_skills_dir_is_class_reachable_and_exists():
    skills = Path(AirflowPlugin.skills_dir())
    assert (skills / "airflow" / "SKILL.md").is_file()
    assert (skills / "airflow-setup" / "SKILL.md").is_file()


def test_system_prompt_unconfigured_points_to_setup_skill():
    text = AirflowPlugin.system_prompt({})
    assert "not configured" in text
    assert "airflow-setup" in text


def test_system_prompt_lists_environments_without_secrets():
    profiles = {
        "prod": {
            "name": "prod",
            "api_base_url": "https://airflow.example.com",
            "username": "admin",
            "password": "s3cr3t-password",
            "token": None,
            "dags_folder": "s3://bucket/dags/",
        },
        "staging": {
            "name": "staging",
            "api_base_url": "http://localhost:8080",
            "token": "very-secret-jwt",
        },
    }
    text = AirflowPlugin.system_prompt(profiles)
    assert "## Airflow" in text
    assert "https://airflow.example.com" in text
    assert "s3://bucket/dags/" in text
    assert "username/password" in text  # auth mode, not the values
    assert "s3cr3t-password" not in text
    assert "very-secret-jwt" not in text
    assert "admin" not in text


def test_config_schema_lists_fields():
    schema = AirflowPlugin.config_schema()
    assert isinstance(schema, list) and schema
    for field in schema:
        assert field.get("name") and field.get("description")
    names = {f["name"] for f in schema}
    assert "api_base_url" in names
    assert any(f["name"] == "api_base_url" and f.get("required") for f in schema)
    secret = {f["name"] for f in schema if f.get("secret")}
    assert {"token", "password"} <= secret  # masked in the form


def test_validate_profile_shape_checks():
    # api_base_url is required
    errs = AirflowPlugin.validate_profile({})
    assert errs and "api_base_url" in errs[0]
    # ${ENV_VAR} placeholders are opaque; every declared field is accepted
    assert AirflowPlugin.validate_profile({"api_base_url": "${AF_URL}"}) == []
    assert AirflowPlugin.validate_profile(
        {f["name"]: "${X}" for f in AirflowPlugin.config_schema()}
    ) == []
    # a malformed base URL is caught
    assert AirflowPlugin.validate_profile({"api_base_url": "ftp://x"})


# ----------------------------------------------------------- cli_permissions


def _subparser_choices(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _cli_command_paths() -> list[str]:
    """All `<group> <subcommand>` (or bare `<group>`) paths of the real parser."""
    from datus_airflow_plugin.cli import build_parser

    paths = []
    for group, group_parser in _subparser_choices(build_parser()).items():
        subs = _subparser_choices(group_parser)
        if subs:
            paths.extend(f"{group} {name}" for name in subs)
        else:
            paths.append(group)
    return paths


def _pattern_head(pattern: str) -> str:
    return pattern.split(":", 1)[0]


def _matches(command: str, head: str) -> bool:
    return command == head or command.startswith(head + " ")


def test_cli_permissions_shape():
    perms = AirflowPlugin.cli_permissions()
    assert set(perms) == {"normal", "auto"}  # `dangerous` must not be declared
    for profile, rules in perms.items():
        assert set(rules) <= {"allow", "ask", "deny"}
        for patterns in rules.values():
            for pattern in patterns:
                assert isinstance(pattern, str) and pattern
                assert not pattern.startswith("datus"), "patterns are namespace-relative"
                assert not pattern.startswith("-")


def test_cli_permissions_patterns_reference_real_commands():
    commands = _cli_command_paths()
    perms = AirflowPlugin.cli_permissions()
    for rules in perms.values():
        for patterns in rules.values():
            for pattern in patterns:
                head = _pattern_head(pattern)
                assert any(_matches(cmd, head) for cmd in commands), f"stale pattern: {pattern}"


def test_cli_permissions_cover_every_command():
    """A new subcommand must be classified, or this test fails."""
    perms = AirflowPlugin.cli_permissions()
    for profile, rules in perms.items():
        heads = [_pattern_head(p) for patterns in rules.values() for p in patterns]
        for command in _cli_command_paths():
            assert any(_matches(command, h) for h in heads), (
                f"{command!r} is not classified in the {profile} profile"
            )


def test_cli_permissions_dangerous_commands_never_auto_run():
    risky = [
        "dags delete", "dags deploy", "dags undeploy", "dags trigger", "assets materialize",
        "backfill create", "variables delete", "pools delete",
        "variables import", "connections import", "pools import",
        "connections add", "connections delete", "connections export",
    ]
    perms = AirflowPlugin.cli_permissions()
    for profile, rules in perms.items():
        allow_heads = [_pattern_head(p) for p in rules.get("allow", [])]
        for command in risky:
            assert not any(_matches(command, h) for h in allow_heads), (
                f"{command!r} must never be auto-run ({profile} profile)"
            )


def test_cli_permissions_read_only_allowed_and_routine_promoted():
    perms = AirflowPlugin.cli_permissions()

    def allowed(profile: str, command: str) -> bool:
        heads = [_pattern_head(p) for p in perms[profile]["allow"]]
        return any(_matches(command, h) for h in heads)

    for command in ("dags list", "tasks logs", "connections get", "version", "jobs check"):
        assert allowed("normal", command) and allowed("auto", command)
    for command in ("dags pause", "tasks clear", "variables set"):
        assert not allowed("normal", command)
        assert allowed("auto", command)


def test_package_never_imports_datus():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import datus_airflow_plugin.plugin; "
         "import datus_airflow_plugin.cli; "
         "assert not any(m == 'datus' or m.startswith('datus.') for m in sys.modules), "
         "'plugin must not import datus'"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    grep = subprocess.run(
        ["grep", "-rn", "import datus", str(PKG_DIR)],
        capture_output=True,
        text=True,
    )
    offending = [
        line
        for line in grep.stdout.splitlines()
        if "datus_airflow_plugin" not in line.split(":", 2)[2]
    ]
    assert not offending, offending
