from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from datus_superset_plugin.operations import OPERATIONS, READ_METHODS
from datus_superset_plugin.cli import build_parser

ROOT = Path(__file__).parents[1]
PKG = ROOT / "datus_superset_plugin"


def _manifest():
    return yaml.safe_load((PKG / "datus-plugin.yml").read_text())


def test_distribution_contract_and_independence():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    points = project["project"]["entry-points"]["datus.plugins"]
    assert points == {"superset": "datus_superset_plugin"}
    assert all("datus" not in dep.lower() for dep in project["project"]["dependencies"])
    for path in PKG.glob("*.py"):
        text = path.read_text()
        assert not re.search(r"(?:from|import)\s+datus(?:\.|\s|$)", text)
        assert "datus_grafana_plugin" not in text


def test_manifest_prompt_and_single_file_skills():
    data = _manifest()
    assert data["manifest_version"] == 1
    assert data["cli"] == "datus_superset_plugin.cli:main"
    assert (PKG / data["system_prompt"]).is_file()
    skill_dirs = sorted((PKG / data["skills"]).iterdir())
    assert {p.name for p in skill_dirs} == {
        "superset", "superset-setup", "superset-dashboard-authoring", "superset-query-export"
    }
    assert all([p.name for p in directory.iterdir()] == ["SKILL.md"] for directory in skill_dirs)
    assert "requires_mutable_config: true" in (PKG / "skills/superset-setup/SKILL.md").read_text()
    schema = data["config_schema"]
    assert schema["properties"]["password"]["x-secret"] is True
    assert schema["properties"]["access_token"]["x-secret"] is True

    env = Environment(loader=FileSystemLoader(PKG), undefined=StrictUndefined)
    template = env.get_template(data["system_prompt"])
    configured = template.render(
        plugin_name="superset", profiles={"prod": {"api_base_url": "https://bi", "auth_mode": "token"}},
        config_path=None, config_mutable=True,
    )
    assert "https://bi" in configured and "token" in configured
    assert "superset-setup" in template.render(plugin_name="superset", profiles={}, config_path=None, config_mutable=True)
    assert "superset-setup" not in template.render(plugin_name="superset", profiles={}, config_path=None, config_mutable=False)


def test_permissions_cover_typed_operations_and_mutations_ask():
    permissions = _manifest()["permissions"]
    for profile in ("normal", "auto"):
        allow = permissions[profile].get("allow", [])
        ask = permissions[profile].get("ask", [])
        for group, operations in OPERATIONS.items():
            for command, operation in operations.items():
                prefix = f"{group} {command}:"
                rules = [rule for rule in allow + ask if rule.startswith(prefix) or rule == f"{group}:*"]
                assert rules, (profile, group, command)
                if operation.method not in READ_METHODS or operation.upload:
                    assert any(rule.startswith(prefix) or rule == f"{group}:*" for rule in ask), (profile, group, command)
    assert "context export-dashboard:*" in permissions["normal"]["ask"]
    assert "api call:*" in permissions["auto"]["ask"]


def test_command_catalogue_matches_operation_registry():
    catalogue = {item["name"]: item for item in _manifest()["commands"]}
    for group, operations in OPERATIONS.items():
        assert {item["name"] for item in catalogue[group]["subcommands"]} == set(operations)


def test_dashboard_export_catalogue_and_parser_support_selective_charts():
    context = next(item for item in _manifest()["commands"] if item["name"] == "context")
    assert "context candidates:*" in _manifest()["permissions"]["normal"]["allow"]
    assert "candidates" in {item["name"] for item in context["subcommands"]}
    export = next(item for item in context["subcommands"] if item["name"] == "export-dashboard")
    assert "--chart-id" in {item["name"] for item in export["args"]}

    parsed = build_parser().parse_args(
        ["context", "export-dashboard", "42", "--chart-id", "11", "--chart-id", "12"]
    )
    assert parsed.chart_id == ["11", "12"]

    candidates = build_parser().parse_args(["context", "candidates", "42"])
    assert candidates.dashboard_id == "42"


def test_profile_and_cli_have_no_instance_level_serving_mapping():
    data = _manifest()
    properties = data["config_schema"]["properties"]
    commands = {item["name"] for item in data["commands"]}
    assert "serving_datasource" not in properties
    assert "serving_database_name" not in properties
    assert "serving-target" not in commands
