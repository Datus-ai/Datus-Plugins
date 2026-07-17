"""The declarative Datus plugin contract (datus-plugin.yml) for datus-cloudwatch-plugin."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from importlib import import_module
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

PLUGIN_NAME = "cloudwatch"
PKG_DIR = Path(__file__).resolve().parents[1] / "datus_cloudwatch_plugin"
GROUPS = ("logs", "metrics", "alarms", "dashboards")


def load_manifest() -> dict:
    return yaml.safe_load((PKG_DIR / "datus-plugin.yml").read_text(encoding="utf-8"))


def resolve_cli(manifest: dict):
    module_name, func_name = manifest["cli"].split(":")
    return getattr(import_module(module_name), func_name)


def render_prompt(manifest: dict, profiles: dict, config_mutable: bool = True) -> str:
    """Render the system-prompt template the way datus does: profiles are
    whitelist-stripped against config_schema (x-secret and undeclared fields
    never reach the template), StrictUndefined, trim/lstrip blocks."""
    schema_props = manifest["config_schema"]["properties"]
    allowed = {name for name, spec in schema_props.items() if spec.get("x-secret") is not True}
    stripped = {name: {k: v for k, v in cfg.items() if k in allowed} for name, cfg in profiles.items()}
    env = Environment(
        loader=FileSystemLoader(str(PKG_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(manifest["system_prompt"])
    return template.render(plugin_name=PLUGIN_NAME, profiles=stripped, config_path=None, config_mutable=config_mutable).strip()


# ------------------------------------------------------------------ manifest


def test_manifest_declares_the_module_contract():
    manifest = load_manifest()
    assert manifest["manifest_version"] == 1
    assert manifest["cli"] == "datus_cloudwatch_plugin.cli:main"
    assert isinstance(manifest["description"], str) and "\n" not in manifest["description"].strip()
    assert (PKG_DIR / manifest["skills"]).is_dir()
    assert (PKG_DIR / manifest["system_prompt"]).is_file()


def test_entry_point_is_a_module_ref():
    pyproject = tomllib.loads((PKG_DIR.parent / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = pyproject["project"]["entry-points"]["datus.plugins"]
    assert entry_points == {PLUGIN_NAME: "datus_cloudwatch_plugin"}


def test_cli_ref_resolves_and_help_returns_zero(capsys):
    main = resolve_cli(load_manifest())
    assert main(["--help"], {}) == 0
    out = capsys.readouterr().out
    for group in GROUPS:
        assert group in out


def test_cli_unknown_command_returns_usage_error():
    main = resolve_cli(load_manifest())
    assert main(["frobnicate"], {}) == 2


def test_cli_unknown_profile_key_is_config_error(capsys):
    main = resolve_cli(load_manifest())
    rc = main(["logs", "groups"], {"bogus_key": 1})
    assert rc == 3
    assert "bogus_key" in capsys.readouterr().err


def test_skills_dir_bundles_main_and_setup_skills():
    skills = PKG_DIR / load_manifest()["skills"]
    assert (skills / "cloudwatch" / "SKILL.md").is_file()
    assert (skills / "cloudwatch-setup" / "SKILL.md").is_file()


# ------------------------------------------------------------- system prompt


def test_system_prompt_unconfigured_points_to_setup_skill():
    text = render_prompt(load_manifest(), {})
    assert "not configured" in text
    assert "cloudwatch-setup" in text


def test_system_prompt_unconfigured_immutable_defers_to_admin():
    # Read-only config deployments must not be pointed at the setup skill.
    text = render_prompt(load_manifest(), {}, config_mutable=False)
    assert "not configured" in text
    assert "cloudwatch-setup" not in text
    assert "administrator" in text


def test_system_prompt_lists_environments_without_secrets():
    profiles = {
        "prod": {
            "name": "prod",
            "region": "us-east-1",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "s3cr3t-key-value",
            "session_token": "t0ken-value",
            "role_arn": "arn:aws:iam::1:role/r",
        },
    }
    text = render_prompt(load_manifest(), profiles)
    assert "## CloudWatch" in text
    assert "us-east-1" in text
    assert "arn:aws:iam::1:role/r" in text
    assert "creds=keys" in text
    assert "s3cr3t-key-value" not in text and "AKIAEXAMPLE" not in text and "t0ken-value" not in text


def test_config_schema_marks_secret_fields():
    schema = load_manifest()["config_schema"]
    assert schema["type"] == "object"
    props = schema["properties"]
    assert props["secret_access_key"]["x-secret"] is True
    assert props["session_token"]["x-secret"] is True
    assert "x-secret" not in props["region"]
    # every non-secret field the prompt template references must be declared
    for referenced in ("region", "profile", "role_arn", "access_key_id"):
        assert referenced in props



# ----------------------------------------------------------- cli permissions


def _subparser_choices(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _cli_command_paths() -> list:
    from datus_cloudwatch_plugin.cli import build_parser

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
    perms = load_manifest()["permissions"]
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
    for rules in load_manifest()["permissions"].values():
        for patterns in rules.values():
            for pattern in patterns:
                head = _pattern_head(pattern)
                assert any(_matches(cmd, head) for cmd in commands), f"stale pattern: {pattern}"


def test_cli_permissions_cover_every_command():
    perms = load_manifest()["permissions"]
    for profile, rules in perms.items():
        heads = [_pattern_head(p) for patterns in rules.values() for p in patterns]
        for command in _cli_command_paths():
            assert any(_matches(command, h) for h in heads), (
                f"{command!r} is not classified in the {profile} profile"
            )


def test_cli_permissions_routine_promoted():
    perms = load_manifest()["permissions"]

    def allowed(profile: str, command: str) -> bool:
        heads = [_pattern_head(p) for p in perms[profile]["allow"]]
        return any(_matches(command, h) for h in heads)

    for command in ("logs groups", "metrics get", "alarms list", "dashboards get"):
        assert allowed("normal", command) and allowed("auto", command)
    # set-state is routine: confirmed under normal, auto-run under auto
    assert not allowed("normal", "alarms set-state")
    assert allowed("auto", "alarms set-state")


def test_package_never_imports_datus():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import datus_cloudwatch_plugin.cli; "
            "assert not any(m == 'datus' or m.startswith('datus.') for m in sys.modules), "
            "'plugin must not import datus'",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
