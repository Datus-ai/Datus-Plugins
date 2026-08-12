from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from datus_grafana_plugin.operations import OPERATIONS, READ_METHODS

ROOT = Path(__file__).parents[1]
PKG = ROOT / "datus_grafana_plugin"


def manifest():
    return yaml.safe_load((PKG / "datus-plugin.yml").read_text())


def test_distribution_contract_and_no_common_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["entry-points"]["datus.plugins"] == {"grafana": "datus_grafana_plugin"}
    assert all("datus" not in dep.lower() for dep in project["project"]["dependencies"])
    for path in PKG.glob("*.py"):
        text = path.read_text()
        assert not re.search(r"(?:from|import)\s+datus(?:\.|\s|$)", text)
        assert "datus_superset_plugin" not in text


def test_manifest_prompt_and_skills():
    data = manifest()
    assert data["manifest_version"] == 1
    assert data["cli"] == "datus_grafana_plugin.cli:main"
    dirs = sorted((PKG / data["skills"]).iterdir())
    assert {p.name for p in dirs} == {"grafana", "grafana-setup", "grafana-dashboard-authoring", "grafana-query-export"}
    assert all([p.name for p in directory.iterdir()] == ["SKILL.md"] for directory in dirs)
    assert "requires_mutable_config: true" in (PKG / "skills/grafana-setup/SKILL.md").read_text()
    assert data["config_schema"]["properties"]["token"]["x-secret"] is True
    assert data["config_schema"]["properties"]["password"]["x-secret"] is True
    env = Environment(loader=FileSystemLoader(PKG), undefined=StrictUndefined)
    template = env.get_template(data["system_prompt"])
    assert "https://g" in template.render(plugin_name="grafana", profiles={"prod": {"api_base_url": "https://g", "auth_mode": "token"}}, config_path=None, config_mutable=True)
    assert "grafana-setup" in template.render(plugin_name="grafana", profiles={}, config_path=None, config_mutable=True)
    assert "grafana-setup" not in template.render(plugin_name="grafana", profiles={}, config_path=None, config_mutable=False)


def test_permissions_cover_typed_operations_and_writes_ask():
    data = manifest()["permissions"]
    for profile in ("normal", "auto"):
        allow, ask = data[profile]["allow"], data[profile]["ask"]
        for group, operations in OPERATIONS.items():
            for command, operation in operations.items():
                prefix = f"{group} {command}:"
                assert any(rule.startswith(prefix) or rule == f"{group}:*" for rule in allow + ask), (profile, group, command)
                if operation.method not in READ_METHODS:
                    assert any(rule.startswith(prefix) or rule == f"{group}:*" for rule in ask), (profile, group, command)
    for rule in ("queries run-panel:*", "panels query:*", "context export-dashboard:*", "api call:*"):
        assert rule in data["normal"]["ask"] and rule in data["auto"]["ask"]


def test_command_catalogue_matches_registry_and_special_commands():
    catalogue = {item["name"]: item for item in manifest()["commands"]}
    for group, operations in OPERATIONS.items():
        declared = {item["name"] for item in catalogue[group]["subcommands"]}
        expected = set(operations)
        if group == "dashboards":
            expected |= {"create", "update", "export"}
        if group == "queries":
            expected.add("run-panel")
        assert declared == expected
