"""The Datus plugin contract: constructor, run_cli, skills_dir, system_prompt, cli_permissions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from datus_statsig_plugin.plugin import StatsigPlugin

PKG_DIR = Path(__file__).resolve().parents[1] / "datus_statsig_plugin"


def test_constructor_accepts_profile_keyword():
    plugin = StatsigPlugin(profile={"name": "prod", "api_key": "x"})
    assert plugin.profile["name"] == "prod"
    assert StatsigPlugin().profile == {}


def test_run_cli_help_returns_zero(capsys):
    rc = StatsigPlugin(profile={}).run_cli(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for group in ("metrics", "metric-source", "experiments", "ingestion", "describe"):
        assert group in out


def test_run_cli_unknown_command_returns_usage_error():
    rc = StatsigPlugin(profile={}).run_cli(["frobnicate"])
    assert rc == 2


def test_run_cli_without_api_key_is_config_error(capsys):
    rc = StatsigPlugin(profile={}).run_cli(["metrics", "list"])
    assert rc == 3
    assert "api_key" in capsys.readouterr().err


def test_missing_required_body_field_is_usage_error(capsys):
    rc = StatsigPlugin(profile={"api_key": "x"}).run_cli(
        ["metric-source", "create", "--json", "{}"]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "missing required field" in err


def test_describe_prints_body_template(capsys):
    rc = StatsigPlugin(profile={}).run_cli(["describe", "metric-source", "create"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "idTypeMapping" in out and "timestampColumn" in out


def test_skills_dir_is_class_reachable_and_exists():
    skills = Path(StatsigPlugin.skills_dir())
    assert (skills / "statsig" / "SKILL.md").is_file()
    assert (skills / "statsig-setup" / "SKILL.md").is_file()


def test_system_prompt_unconfigured_points_to_setup_skill():
    text = StatsigPlugin.system_prompt({})
    assert "not configured" in text
    assert "statsig-setup" in text


def test_system_prompt_lists_environments_without_secrets():
    profiles = {
        "prod": {
            "name": "prod",
            "api_base_url": "https://statsigapi.net",
            "api_version": "20240601",
            "api_key": "console-secret-key",
        },
        "staging": {"name": "staging", "api_key": "another-secret"},
    }
    text = StatsigPlugin.system_prompt(profiles)
    assert "## Statsig" in text
    assert "https://statsigapi.net" in text
    assert "20240601" in text
    assert "console-secret-key" not in text
    assert "another-secret" not in text


# ----------------------------------------------------------- cli_permissions


def _subparser_choices(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _cli_command_paths() -> list[str]:
    """All `<group> <subcommand>` (or bare `<group>`) paths of the real parser."""
    from datus_statsig_plugin.cli import build_parser

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
    perms = StatsigPlugin.cli_permissions()
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
    perms = StatsigPlugin.cli_permissions()
    for rules in perms.values():
        for patterns in rules.values():
            for pattern in patterns:
                head = _pattern_head(pattern)
                assert any(_matches(cmd, head) for cmd in commands), f"stale pattern: {pattern}"


def test_cli_permissions_cover_every_command():
    """A new subcommand must be classified, or this test fails."""
    perms = StatsigPlugin.cli_permissions()
    for profile, rules in perms.items():
        heads = [_pattern_head(p) for patterns in rules.values() for p in patterns]
        for command in _cli_command_paths():
            assert any(_matches(command, h) for h in heads), (
                f"{command!r} is not classified in the {profile} profile"
            )


def test_cli_permissions_mutations_never_auto_run():
    risky = [
        "metrics create", "metrics update", "metrics reload",
        "metric-source create", "metric-source update", "metric-source delete",
        "experiments load-pulse", "ingestion backfill", "ingestion schedule-set",
        "warehouse-connections update",
    ]
    perms = StatsigPlugin.cli_permissions()
    for profile, rules in perms.items():
        allow_heads = [_pattern_head(p) for p in rules.get("allow", [])]
        for command in risky:
            assert not any(_matches(command, h) for h in allow_heads), (
                f"{command!r} must never be auto-run ({profile} profile)"
            )


def test_cli_permissions_reads_allowed_everywhere():
    perms = StatsigPlugin.cli_permissions()

    def allowed(profile: str, command: str) -> bool:
        heads = [_pattern_head(p) for p in perms[profile]["allow"]]
        return any(_matches(command, h) for h in heads)

    for command in (
        "metrics list", "metrics sql", "experiments pulse", "ingestion runs",
        "logs query", "reports get", "describe",
    ):
        assert allowed("normal", command) and allowed("auto", command)


def test_package_never_imports_datus():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import datus_statsig_plugin.plugin; import datus_statsig_plugin.cli; "
            "assert not any(m == 'datus' or m.startswith('datus.') for m in sys.modules), "
            "'plugin must not import datus'",
        ],
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
        if "datus_statsig_plugin" not in line.split(":", 2)[2]
    ]
    assert not offending, offending
