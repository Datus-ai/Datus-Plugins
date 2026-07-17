"""The Datus plugin contract: the declarative ``datus-plugin.yml`` manifest.

The plugin ships no class — datus reads the manifest (entry point value ==
package name), calls the declared ``cli`` function as ``main(argv, profile)``,
renders the declared Jinja2 system-prompt template with secret-stripped
profiles, and applies the declared bash-permission rules. These tests validate
each declared surface with plain YAML/Jinja2 tooling, emulating datus'
renderer settings (StrictUndefined, trim_blocks, lstrip_blocks) and its
structural secret stripping.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from datus_airflow_plugin.cli import main

PKG_DIR = Path(__file__).resolve().parents[1] / "datus_airflow_plugin"
MANIFEST: Dict[str, Any] = yaml.safe_load((PKG_DIR / "datus-plugin.yml").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ manifest


def test_entry_point_is_a_bare_package_name():
    pyproject = tomllib.loads((PKG_DIR.parent / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = pyproject["project"]["entry-points"]["datus.plugins"]
    assert entry_points == {"airflow": "datus_airflow_plugin"}  # pkg:Class refs are the legacy contract


def test_manifest_declares_the_new_contract():
    assert MANIFEST["manifest_version"] == 1
    assert MANIFEST["cli"] == "datus_airflow_plugin.cli:main"
    description = MANIFEST["description"]
    assert isinstance(description, str) and description.strip() and "\n" not in description


def test_manifest_paths_exist_in_the_package():
    skills = PKG_DIR / MANIFEST["skills"]
    assert (skills / "airflow" / "SKILL.md").is_file()
    assert (skills / "airflow-setup" / "SKILL.md").is_file()
    assert (PKG_DIR / MANIFEST["system_prompt"]).is_file()


# ----------------------------------------------------------------- cli entry


def test_cli_help_returns_zero(capsys):
    rc = main(["--help"], {})
    assert rc == 0
    out = capsys.readouterr().out
    for group in ("dags", "tasks", "variables", "connections", "pools", "backfill"):
        assert group in out


def test_cli_unknown_command_returns_usage_error(capsys):
    rc = main(["frobnicate"], {})
    assert rc == 2


def test_cli_without_base_url_is_config_error(capsys):
    rc = main(["dags", "list"], {})
    assert rc == 3
    assert "api_base_url" in capsys.readouterr().err


# ------------------------------------------------------------- config schema


def _schema() -> Dict[str, Any]:
    return MANIFEST["config_schema"]


def _non_secret_fields() -> set:
    return {
        name
        for name, spec in _schema()["properties"].items()
        if not (isinstance(spec, dict) and spec.get("x-secret") is True)
    }


def test_config_schema_is_a_valid_json_schema():
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(_schema())


def test_config_schema_marks_credentials_secret():
    properties = _schema()["properties"]
    for secret_field in ("token", "password"):
        assert properties[secret_field]["x-secret"] is True, f"{secret_field} must never reach the prompt"
    s3_properties = properties["s3"]["properties"]
    for secret_field in ("secret_access_key", "session_token"):
        assert s3_properties[secret_field]["x-secret"] is True, f"s3.{secret_field} must never reach the prompt"
    # The s3 block itself stays non-secret so its non-credential leaves are
    # TUI-editable (they surface as dotted fields: s3.region, ...).
    assert {"api_base_url", "username", "dags_folder", "s3"} <= _non_secret_fields()


def test_config_schema_s3_matches_the_runtime_keys():
    """S3Settings.from_dict rejects unknown keys; the schema must declare
    exactly that key set (and additionalProperties: false) so TUI validation
    mirrors the runtime."""
    from datus_airflow_plugin.config import S3Settings

    s3_schema = _schema()["properties"]["s3"]
    assert s3_schema["additionalProperties"] is False
    assert set(s3_schema["properties"]) == set(S3Settings.__dataclass_fields__)


def test_config_schema_accepts_a_real_profile_and_requires_api_base_url():
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(_schema())
    profile = {
        "name": "prod",  # datus injects the profile name; the schema must tolerate it
        "api_base_url": "https://airflow.example.com",
        "username": "admin",
        "password": "${AIRFLOW_PASSWORD}",
        "verify_ssl": True,
        "timeout": 30,
        "dags_folder": "s3://bucket/dags/",
        "s3": {"region": "us-east-1", "secret_access_key": "${AWS_SECRET_ACCESS_KEY}"},
    }
    assert list(validator.iter_errors(profile)) == []
    errors = [e.message for e in validator.iter_errors({"username": "admin"})]
    assert any("api_base_url" in message for message in errors)


def test_config_schema_rejects_unknown_s3_keys():
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(_schema())
    profile = {"api_base_url": "https://airflow.example.com", "s3": {"regoin": "us-east-1"}}
    errors = [e.message for e in validator.iter_errors(profile)]
    assert any("regoin" in message for message in errors)


# -------------------------------------------------------------- prompt


def _whitelist(cfg: Dict[str, Any], properties: Dict[str, Any]) -> Dict[str, Any]:
    """Emulate datus' structural whitelist: only declared, non-x-secret schema
    fields survive, recursing into declared nested objects."""
    kept: Dict[str, Any] = {}
    for key, value in (cfg or {}).items():
        spec = properties.get(key)
        if spec is None or (isinstance(spec, dict) and spec.get("x-secret") is True):
            continue
        nested = spec.get("properties") if isinstance(spec, dict) else None
        if isinstance(spec, dict) and spec.get("type") == "object" and isinstance(nested, dict):
            kept[key] = _whitelist(value if isinstance(value, dict) else {}, nested)
        else:
            kept[key] = value
    return kept


def _strip_secret_fields(profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    properties = _schema()["properties"]
    return {name: _whitelist(cfg, properties) for name, cfg in profiles.items()}


def _render_prompt(profiles: Dict[str, Dict[str, Any]], config_mutable: bool = True) -> str:
    env = Environment(
        loader=FileSystemLoader(str(PKG_DIR)),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(MANIFEST["system_prompt"])
    return template.render(plugin_name="airflow", profiles=_strip_secret_fields(profiles), config_path=None, config_mutable=config_mutable).strip()


def test_prompt_unconfigured_points_to_setup_skill():
    text = _render_prompt({})
    assert "not configured" in text
    assert "airflow-setup" in text


def test_prompt_unconfigured_immutable_defers_to_admin():
    # Read-only config deployments must not be pointed at the setup skill.
    text = _render_prompt({}, config_mutable=False)
    assert "not configured" in text
    assert "airflow-setup" not in text
    assert "administrator" in text


def test_prompt_lists_environments_without_secrets():
    profiles = {
        "prod": {
            "name": "prod",
            "api_base_url": "https://airflow.example.com",
            "username": "admin",
            "password": "s3cr3t-password",
            "dags_folder": "s3://bucket/dags/",
            "s3": {"access_key_id": "AKIAXXX", "secret_access_key": "aws-secret-key"},
        },
        "staging": {
            "name": "staging",
            "api_base_url": "http://localhost:8080",
            "token": "very-secret-jwt",
        },
    }
    text = _render_prompt(profiles)
    assert "## Airflow" in text
    assert "Environments (2):" in text
    assert "https://airflow.example.com" in text
    assert "s3://bucket/dags/" in text
    assert "username/password" in text  # auth mode, not the values
    assert "auth=token" in text  # staging has no username
    for secret in ("s3cr3t-password", "very-secret-jwt", "AKIAXXX", "aws-secret-key", "admin"):
        assert secret not in text
    assert "- prod: " in text and "- staging: " in text  # one line per environment


def test_prompt_handles_a_profile_missing_optional_fields():
    # StrictUndefined: cfg.get() must guard every optional field.
    text = _render_prompt({"bare": {"name": "bare"}})
    assert "- bare: api=?, auth=token" in text
    assert "dags_folder" not in text.split("Environments", 1)[1]


# ------------------------------------------------------------------ permissions


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


def _permissions() -> Dict[str, Dict[str, list]]:
    return MANIFEST["permissions"]


def test_permissions_shape():
    perms = _permissions()
    assert set(perms) == {"normal", "auto"}  # `dangerous` must not be declared
    for profile, rules in perms.items():
        assert set(rules) <= {"allow", "ask", "deny"}
        for patterns in rules.values():
            for pattern in patterns:
                assert isinstance(pattern, str) and pattern
                assert not pattern.startswith("datus"), "patterns are namespace-relative"
                assert not pattern.startswith("-")


def test_permissions_patterns_reference_real_commands():
    commands = _cli_command_paths()
    for rules in _permissions().values():
        for patterns in rules.values():
            for pattern in patterns:
                head = _pattern_head(pattern)
                assert any(_matches(cmd, head) for cmd in commands), f"stale pattern: {pattern}"


def test_permissions_cover_every_command():
    """A new subcommand must be classified, or this test fails."""
    for profile, rules in _permissions().items():
        heads = [_pattern_head(p) for patterns in rules.values() for p in patterns]
        for command in _cli_command_paths():
            assert any(_matches(command, h) for h in heads), (
                f"{command!r} is not classified in the {profile} profile"
            )


def test_permissions_dangerous_commands_never_auto_run():
    risky = [
        "dags delete", "dags deploy", "dags undeploy", "dags trigger", "assets materialize",
        "backfill create", "variables delete", "pools delete",
        "variables import", "connections import", "pools import",
        "connections add", "connections delete", "connections export",
    ]
    for profile, rules in _permissions().items():
        allow_heads = [_pattern_head(p) for p in rules.get("allow", [])]
        for command in risky:
            assert not any(_matches(command, h) for h in allow_heads), (
                f"{command!r} must never be auto-run ({profile} profile)"
            )


def test_permissions_read_only_allowed_and_routine_promoted():
    perms = _permissions()

    def allowed(profile: str, command: str) -> bool:
        heads = [_pattern_head(p) for p in perms[profile]["allow"]]
        return any(_matches(command, h) for h in heads)

    for command in ("dags list", "tasks logs", "connections get", "version", "jobs check"):
        assert allowed("normal", command) and allowed("auto", command)
    for command in ("dags pause", "tasks clear", "variables set"):
        assert not allowed("normal", command)
        assert allowed("auto", command)


# ------------------------------------------------------------------ isolation


def test_package_never_imports_datus():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import datus_airflow_plugin; "
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
