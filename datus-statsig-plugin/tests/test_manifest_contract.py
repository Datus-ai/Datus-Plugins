"""The Datus plugin contract: the declarative `datus-plugin.yml` manifest.

The plugin ships no class — datus reads `datus-plugin.yml` from the package
root (without importing the package) and only imports the declared `cli`
function on `datus statsig ...` dispatch. These tests validate the manifest
against the real package: the entry point, the `cli` code ref and its exit
codes, the permission rules, the config schema, the bundled skills, and the
system-prompt template rendered exactly as datus renders it (whitelist-
stripped profiles, StrictUndefined, trim_blocks + lstrip_blocks).
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import yaml

PKG_DIR = Path(__file__).resolve().parents[1] / "datus_statsig_plugin"
MANIFEST_PATH = PKG_DIR / "datus-plugin.yml"
PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"

MANIFEST_KEYS = {
    "manifest_version",
    "description",
    "cli",
    "tool_transformers",
    "permissions",
    "system_prompt",
    "skills",
    "config_schema",
}

# The permission policy, verbatim: reads (and `describe`) auto-run under both
# profiles; every mutation asks under both (never promoted by `auto`).
READ_ONLY = [
    "metrics list:*", "metrics get:*", "metrics sql:*", "metrics values:*",
    "metric-source list:*", "metric-source get:*",
    "experiments list:*", "experiments get:*", "experiments pulse:*",
    "experiments summary:*", "experiments exposures:*", "experiments pulse-status:*",
    "ingestion get:*", "ingestion runs:*", "ingestion run:*", "ingestion status:*",
    "ingestion schedule-get:*",
    "events list:*", "events get:*",
    "logs query:*",
    "reports get:*",
    "describe:*",
]
ALWAYS_ASK = [
    "metrics create:*", "metrics update:*", "metrics reload:*",
    "metric-source create:*", "metric-source update:*", "metric-source delete:*",
    "experiments load-pulse:*",
    "ingestion backfill:*", "ingestion schedule-set:*",
    "warehouse-connections update:*",
]


@pytest.fixture(scope="module")
def manifest() -> Dict[str, Any]:
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "manifest root must be a mapping"
    return data


def resolve_cli(manifest: Dict[str, Any]):
    module_name, attr = manifest["cli"].split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


# ------------------------------------------------------------------ manifest


def test_manifest_parses_with_supported_version(manifest):
    assert manifest["manifest_version"] == 1
    assert set(manifest) <= MANIFEST_KEYS, f"unknown manifest keys: {set(manifest) - MANIFEST_KEYS}"


def test_manifest_description_is_one_line(manifest):
    description = manifest["description"]
    assert isinstance(description, str) and description.strip()
    assert "\n" not in description


def test_entry_point_is_the_package_name():
    with open(PYPROJECT_PATH, "rb") as fh:
        pyproject = tomllib.load(fh)
    entry_points = pyproject["project"]["entry-points"]["datus.plugins"]
    assert entry_points == {"statsig": "datus_statsig_plugin"}
    # A `pkg:Class` ref is the legacy class-based contract and is rejected.
    assert ":" not in entry_points["statsig"]


def test_manifest_ships_at_the_package_root():
    assert MANIFEST_PATH.is_file()


# ----------------------------------------------------------------------- cli


def test_cli_ref_points_at_importable_function(manifest):
    assert manifest["cli"] == "datus_statsig_plugin.cli:main"
    assert callable(resolve_cli(manifest))


def test_cli_help_returns_zero(manifest, capsys):
    main = resolve_cli(manifest)
    rc = main(["--help"], {})
    assert rc == 0
    out = capsys.readouterr().out
    for group in ("metrics", "metric-source", "experiments", "ingestion", "describe"):
        assert group in out


def test_cli_unknown_command_returns_usage_error(manifest):
    assert resolve_cli(manifest)(["frobnicate"], {}) == 2


def test_cli_without_api_key_is_config_error(manifest, capsys):
    rc = resolve_cli(manifest)(["metrics", "list"], {})
    assert rc == 3
    assert "api_key" in capsys.readouterr().err


def test_cli_missing_required_body_field_is_usage_error(manifest, capsys):
    rc = resolve_cli(manifest)(["metric-source", "create", "--json", "{}"], {"api_key": "x"})
    assert rc == 2
    assert "missing required field" in capsys.readouterr().err


def test_cli_describe_prints_body_template(manifest, capsys):
    rc = resolve_cli(manifest)(["describe", "metric-source", "create"], {})
    assert rc == 0
    out = capsys.readouterr().out
    assert "idTypeMapping" in out and "timestampColumn" in out


# ----------------------------------------------------------------- permissions


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


def test_permissions_shape(manifest):
    perms = manifest["permissions"]
    assert set(perms) == {"normal", "auto"}  # `dangerous` must not be declared
    for profile, rules in perms.items():
        assert set(rules) <= {"allow", "ask", "deny"}
        for patterns in rules.values():
            for pattern in patterns:
                assert isinstance(pattern, str) and pattern
                assert not pattern.startswith("datus"), "patterns are namespace-relative"
                assert not pattern.startswith("-")


def test_permissions_preserved_verbatim(manifest):
    """Both profiles carry the exact read-only/always-ask pattern lists."""
    perms = manifest["permissions"]
    for profile in ("normal", "auto"):
        assert perms[profile]["allow"] == READ_ONLY, f"{profile} allow list drifted"
        assert perms[profile]["ask"] == ALWAYS_ASK, f"{profile} ask list drifted"


def test_permissions_patterns_reference_real_commands(manifest):
    commands = _cli_command_paths()
    for rules in manifest["permissions"].values():
        for patterns in rules.values():
            for pattern in patterns:
                head = _pattern_head(pattern)
                assert any(_matches(cmd, head) for cmd in commands), f"stale pattern: {pattern}"


def test_permissions_cover_every_command(manifest):
    """A new subcommand must be classified, or this test fails."""
    for profile, rules in manifest["permissions"].items():
        heads = [_pattern_head(p) for patterns in rules.values() for p in patterns]
        for command in _cli_command_paths():
            assert any(_matches(command, h) for h in heads), (
                f"{command!r} is not classified in the {profile} profile"
            )


def test_permissions_mutations_never_auto_run(manifest):
    risky = [
        "metrics create", "metrics update", "metrics reload",
        "metric-source create", "metric-source update", "metric-source delete",
        "experiments load-pulse", "ingestion backfill", "ingestion schedule-set",
        "warehouse-connections update",
    ]
    for profile, rules in manifest["permissions"].items():
        allow_heads = [_pattern_head(p) for p in rules.get("allow", [])]
        for command in risky:
            assert not any(_matches(command, h) for h in allow_heads), (
                f"{command!r} must never be auto-run ({profile} profile)"
            )


def test_permissions_reads_allowed_everywhere(manifest):
    perms = manifest["permissions"]

    def allowed(profile: str, command: str) -> bool:
        heads = [_pattern_head(p) for p in perms[profile]["allow"]]
        return any(_matches(command, h) for h in heads)

    for command in (
        "metrics list", "metrics sql", "experiments pulse", "ingestion runs",
        "logs query", "reports get", "describe",
    ):
        assert allowed("normal", command) and allowed("auto", command)


# --------------------------------------------------------------- config schema


def test_config_schema_is_a_valid_object_schema(manifest):
    from jsonschema import Draft202012Validator

    schema = manifest["config_schema"]
    Draft202012Validator.check_schema(schema)  # datus drops invalid schemas
    assert schema["type"] == "object"
    assert schema["required"] == ["api_key"]
    properties = schema["properties"]
    # TUI-entered values are strings.
    assert all(spec["type"] == "string" for spec in properties.values())
    assert properties["api_key"]["x-secret"] is True
    assert properties["api_base_url"]["default"] == "https://statsigapi.net"
    assert properties["api_version"]["default"] == "20240601"


def test_config_schema_declares_every_field_the_template_reads(manifest):
    """Undeclared fields are stripped before rendering — the prompt template
    may only reference non-secret fields declared in the schema."""
    properties = manifest["config_schema"]["properties"]
    non_secret = {k for k, spec in properties.items() if spec.get("x-secret") is not True}
    for field in ("api_base_url", "base_url", "api_version"):
        assert field in non_secret, f"template reads {field!r}, schema must declare it non-secret"


def test_config_schema_validates_a_typical_profile(manifest):
    from jsonschema import Draft202012Validator, ValidationError

    validator = Draft202012Validator(manifest["config_schema"])
    validator.validate({"api_key": "${STATSIG_CONSOLE_API_KEY}", "api_base_url": "https://statsigapi.net"})
    with pytest.raises(ValidationError):  # api_key is required
        validator.validate({"api_base_url": "https://statsigapi.net"})


# --------------------------------------------------------------- system prompt


def _strip_secret_fields(profiles: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Mirror datus' structural secret stripping: whitelist schema-declared,
    non-``x-secret`` fields; undeclared fields never reach the template."""
    allowed = {
        name
        for name, spec in (schema or {}).get("properties", {}).items()
        if not (isinstance(spec, dict) and spec.get("x-secret") is True)
    }
    return {name: {k: v for k, v in (cfg or {}).items() if k in allowed} for name, cfg in profiles.items()}


def _render(manifest: Dict[str, Any], profiles: Dict[str, Any]) -> str:
    """Render the template exactly as datus does."""
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    env = Environment(
        loader=FileSystemLoader(str(PKG_DIR)),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(manifest["system_prompt"])
    return template.render(
        plugin_name="statsig",
        profiles=_strip_secret_fields(profiles, manifest["config_schema"]),
        config_path=None,
    ).strip()


def test_system_prompt_template_exists_inside_the_package(manifest):
    assert manifest["system_prompt"] == "prompts/system.md.j2"
    template_path = (PKG_DIR / manifest["system_prompt"]).resolve()
    assert template_path.is_file()
    assert template_path.is_relative_to(PKG_DIR.resolve())


def test_system_prompt_unconfigured_points_to_setup_skill(manifest):
    text = _render(manifest, {})
    assert "not configured" in text
    assert "statsig-setup" in text


def test_system_prompt_lists_environments_without_secrets(manifest):
    profiles = {
        "prod": {
            "api_base_url": "https://statsigapi.net",
            "api_version": "20240601",
            "api_key": "console-secret-key",
        },
        "staging": {"api_key": "another-secret", "undeclared_field": "also-stripped"},
    }
    text = _render(manifest, profiles)
    assert "## Statsig" in text
    assert "Environments (2):" in text
    assert "- prod: api=https://statsigapi.net, version=20240601" in text
    assert "console-secret-key" not in text
    assert "another-secret" not in text
    assert "also-stripped" not in text


def test_system_prompt_defaults_apply_when_profile_has_no_values(manifest):
    text = _render(manifest, {"staging": {"api_key": "secret"}})
    assert "- staging: api=https://statsigapi.net, version=20240601" in text


def test_system_prompt_honours_legacy_base_url_alias(manifest):
    text = _render(manifest, {"eu": {"base_url": "https://eu.example.net", "api_key": "secret"}})
    assert "- eu: api=https://eu.example.net, version=20240601" in text


# --------------------------------------------------------------------- skills


def test_bundled_skills_exist(manifest):
    assert manifest["skills"] == "skills"
    skills = PKG_DIR / manifest["skills"]
    assert (skills / "statsig" / "SKILL.md").is_file()
    assert (skills / "statsig-setup" / "SKILL.md").is_file()


# -------------------------------------------------------------------- hygiene


def test_package_never_imports_datus():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import datus_statsig_plugin.cli; import datus_statsig_plugin.config; "
            "import datus_statsig_plugin.client; "
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


def test_no_plugin_class_module_remains():
    assert not (PKG_DIR / "plugin.py").exists(), "legacy class-based contract module must be gone"
