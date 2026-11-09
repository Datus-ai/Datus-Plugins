from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import pytest
import yaml

from datus_tableau_plugin import cli
from datus_tableau_plugin.cli import build_parser, main
from test_sync import Client

ROOT = Path(__file__).parents[1]
PKG = ROOT / "datus_tableau_plugin"


def test_distribution_and_recursive_source_independence():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["entry-points"]["datus.plugins"] == {
        "tableau": "datus_tableau_plugin"
    }
    assert all(
        not dep.lower().startswith("datus")
        for dep in project["project"]["dependencies"]
    )
    for path in PKG.rglob("*.py"):
        assert not re.search(r"(?:from|import)\s+datus(?:\.|\s|$)", path.read_text())
        assert "datus_superset_plugin" not in path.read_text()


def test_catalogue_and_permissions_cover_parser_leaves():
    manifest = yaml.safe_load((PKG / "datus-plugin.yml").read_text())
    assert manifest["manifest_version"] == 1
    assert manifest["cli"] == "datus_tableau_plugin.cli:main"

    def catalogue(items, prefix=()):
        return set().union(
            *(
                catalogue(item["subcommands"], (*prefix, item["name"]))
                if item.get("subcommands")
                else {(*prefix, item["name"])}
                for item in items
            )
        )

    def parser_leaves(parser, prefix=()):
        action = next(
            (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
            None,
        )
        return (
            set().union(
                *(
                    parser_leaves(child, (*prefix, name))
                    for name, child in action.choices.items()
                )
            )
            if action
            else {prefix}
        )

    leaves = parser_leaves(build_parser())
    assert catalogue(manifest["commands"]) == leaves
    assert ("sync", "run") in leaves
    for profile in ("normal", "auto"):
        perms = manifest["permissions"][profile]
        for leaf in leaves:
            rule = " ".join(leaf) + ":*"
            assert rule in perms["allow"] + perms["ask"]
        assert perms["ask"] == [
            "sync run:*",
            "plan:*",
            "context export-dashboard:*",
            "api call:*",
        ]
        assert ":*" not in perms["allow"]
    properties = manifest["config_schema"]["properties"]
    assert properties["password"]["x-secret"] is True
    assert properties["token_secret"]["x-secret"] is True


def test_help_usage_and_config_exit_codes_without_network(capsys):
    assert main(["--help"], {}) == 0
    assert "sync" in capsys.readouterr().out
    assert main(["sync", "run"], {}) == 2
    assert main(["sync", "run", "--all", "--project-id", "p"], {}) == 2
    assert main(["status"], {}) == 3
    assert main(["publish"], {}) == 2
    assert main(["flows", "run", "flow"], {}) == 2


def test_sync_then_inspect_is_offline_and_tampering_returns_error(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    source = Client()
    source.__class__.__enter__ = lambda self: self
    source.__class__.__exit__ = lambda *args: None
    monkeypatch.setattr(cli, "TableauClient", lambda settings: source)
    profile = {
        "server_url": "https://tableau.example.test",
        "site_content_url": "",
        "token_name": "automation",
        "token_secret": "never-print",
    }
    assert (
        main(["sync", "run", "--dashboard-id", "dashboard:book1:Overview"], profile)
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        cli,
        "TableauClient",
        lambda settings: pytest.fail("inspect must not create a client"),
    )
    assert main(["sync", "inspect", result["output_dir"]], {}) == 0
    read = json.loads(capsys.readouterr().out)
    assert read["manifest"]["schema_version"] == 1
    (Path(result["output_dir"]) / "assets.json").write_text("[]")
    assert main(["sync", "inspect", result["output_dir"]], {}) == 1
    assert "checksum" in capsys.readouterr().err


def test_sdk_exception_does_not_echo_credentials(monkeypatch, capsys):
    def fail(settings):
        raise RuntimeError("request includes password=private-value")

    monkeypatch.setattr(cli, "TableauClient", fail)
    assert (
        main(
            ["status"],
            {
                "server_url": "https://tableau.example.test",
                "token_name": "n",
                "token_secret": "private-value",
            },
        )
        == 1
    )
    assert "private-value" not in capsys.readouterr().err


def test_prompt_and_single_file_skills():
    manifest = yaml.safe_load((PKG / "datus-plugin.yml").read_text())
    env = Environment(loader=FileSystemLoader(PKG), undefined=StrictUndefined)
    template = env.get_template(manifest["system_prompt"])
    configured = template.render(
        profiles={
            "prod": {
                "server_url": "https://tableau.example.test",
                "site_content_url": "Sales",
                "auth_type": "pat",
            }
        },
        plugin_name="tableau",
        config_path=None,
        config_mutable=True,
    )
    assert "https://tableau.example.test" in configured
    assert "tableau-setup" in template.render(
        profiles={}, plugin_name="tableau", config_path=None, config_mutable=True
    )
    assert "tableau-setup" not in template.render(
        profiles={}, plugin_name="tableau", config_path=None, config_mutable=False
    )
    skill_dirs = list((PKG / manifest["skills"]).iterdir())
    assert {p.name for p in skill_dirs} == {
        "tableau",
        "tableau-setup",
        "tableau-sync",
        "tableau-rebuild",
    }
    assert all(
        [p.name for p in directory.iterdir()] == ["SKILL.md"]
        for directory in skill_dirs
    )
    assert (
        "requires_mutable_config: true"
        in (PKG / "skills/tableau-setup/SKILL.md").read_text()
    )
