"""The Datus plugin contract for datus-ecs-plugin."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from datus_ecs_plugin.plugin import EcsPlugin

PKG_DIR = Path(__file__).resolve().parents[1] / "datus_ecs_plugin"
GROUPS = ("clusters", "services", "tasks", "task-defs")


def test_constructor_accepts_profile_keyword():
    plugin = EcsPlugin(profile={"name": "prod", "region": "us-east-1"})
    assert plugin.profile["name"] == "prod"
    assert EcsPlugin().profile == {}


def test_run_cli_help_returns_zero(capsys):
    rc = EcsPlugin(profile={}).run_cli(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for group in GROUPS:
        assert group in out


def test_run_cli_unknown_command_returns_usage_error():
    assert EcsPlugin(profile={}).run_cli(["frobnicate"]) == 2


def test_run_cli_unknown_profile_key_is_config_error(capsys):
    rc = EcsPlugin(profile={"bogus_key": 1}).run_cli(["clusters", "list"])
    assert rc == 3
    assert "bogus_key" in capsys.readouterr().err


def test_skills_dir_is_class_reachable_and_exists():
    skills = Path(EcsPlugin.skills_dir())
    assert (skills / "ecs" / "SKILL.md").is_file()
    assert (skills / "ecs-setup" / "SKILL.md").is_file()


def test_system_prompt_unconfigured_points_to_setup_skill():
    text = EcsPlugin.system_prompt({})
    assert "not configured" in text
    assert "ecs-setup" in text


def test_system_prompt_lists_environments_without_secrets():
    profiles = {
        "prod": {
            "name": "prod",
            "region": "us-east-1",
            "cluster": "prod-cluster",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "s3cr3t-key-value",
        }
    }
    text = EcsPlugin.system_prompt(profiles)
    assert "## ECS" in text
    assert "us-east-1" in text and "prod-cluster" in text
    assert "s3cr3t-key-value" not in text and "AKIAEXAMPLE" not in text


# ----------------------------------------------------------- cli_permissions


def _subparser_choices(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _cli_command_paths() -> list:
    from datus_ecs_plugin.cli import build_parser

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
    perms = EcsPlugin.cli_permissions()
    assert set(perms) == {"normal", "auto"}
    for rules in perms.values():
        assert set(rules) <= {"allow", "ask", "deny"}
        for patterns in rules.values():
            for pattern in patterns:
                assert isinstance(pattern, str) and pattern
                assert not pattern.startswith("datus")
                assert not pattern.startswith("-")


def test_cli_permissions_patterns_reference_real_commands():
    commands = _cli_command_paths()
    for rules in EcsPlugin.cli_permissions().values():
        for patterns in rules.values():
            for pattern in patterns:
                head = _pattern_head(pattern)
                assert any(_matches(cmd, head) for cmd in commands), f"stale pattern: {pattern}"


def test_cli_permissions_cover_every_command():
    perms = EcsPlugin.cli_permissions()
    for profile, rules in perms.items():
        heads = [_pattern_head(p) for patterns in rules.values() for p in patterns]
        for command in _cli_command_paths():
            assert any(_matches(command, h) for h in heads), (
                f"{command!r} is not classified in the {profile} profile"
            )


def test_cli_permissions_dangerous_never_auto_run():
    perms = EcsPlugin.cli_permissions()
    for profile, rules in perms.items():
        allow_heads = [_pattern_head(p) for p in rules.get("allow", [])]
        assert not any(_matches("tasks run", h) for h in allow_heads), f"tasks run must never auto-run ({profile})"


def test_cli_permissions_routine_promoted():
    perms = EcsPlugin.cli_permissions()

    def allowed(profile: str, command: str) -> bool:
        heads = [_pattern_head(p) for p in perms[profile]["allow"]]
        return any(_matches(command, h) for h in heads)

    for command in ("clusters list", "task-defs list", "tasks logs"):
        assert allowed("normal", command) and allowed("auto", command)
    for command in ("services scale", "tasks stop"):
        assert not allowed("normal", command)
        assert allowed("auto", command)


def test_package_never_imports_datus():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import datus_ecs_plugin.plugin; import datus_ecs_plugin.cli; "
            "assert not any(m == 'datus' or m.startswith('datus.') for m in sys.modules), "
            "'plugin must not import datus'",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
