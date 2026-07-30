"""Contract tests for the declarative Datus k8s plugin."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from importlib import import_module
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

PLUGIN_NAME = "k8s"
ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "datus_k8s_plugin"


def manifest() -> dict:
    return yaml.safe_load((PKG / "datus-plugin.yml").read_text(encoding="utf-8"))


def parser_paths() -> set[str]:
    from datus_k8s_plugin.cli import build_parser

    def choices(parser: argparse.ArgumentParser) -> dict:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action.choices
        return {}

    paths: set[str] = set()
    for name, parser in choices(build_parser()).items():
        nested = choices(parser)
        if nested:
            paths.update(f"{name} {child}" for child in nested)
        else:
            paths.add(name)
    return paths


def catalogue_paths() -> set[str]:
    def walk(commands: list, prefix: str = "") -> set[str]:
        result: set[str] = set()
        for command in commands:
            path = f"{prefix} {command['name']}".strip()
            children = command.get("subcommands") or []
            if children:
                result.update(walk(children, path))
            else:
                result.add(path)
        return result

    return walk(manifest()["commands"])


def test_manifest_and_entry_point_contract():
    data = manifest()
    assert data["manifest_version"] == 1
    assert data["cli"] == "datus_k8s_plugin.cli:main"
    assert (PKG / data["skills"]).is_dir()
    assert (PKG / data["system_prompt"]).is_file()

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["entry-points"]["datus.plugins"] == {
        "k8s": "datus_k8s_plugin"
    }
    assert not any(dep.lower().split(">", 1)[0] == "datus" for dep in pyproject["project"]["dependencies"])


def test_cli_ref_resolves_and_help_does_not_require_configuration(capsys):
    module_name, function_name = manifest()["cli"].split(":")
    main = getattr(import_module(module_name), function_name)
    assert main(["--help"], {}) == 0
    output = capsys.readouterr().out
    assert "get" in output and "apply" in output and "rollout" in output


def test_commands_catalogue_matches_parser():
    assert catalogue_paths() == parser_paths()


def test_permissions_cover_every_leaf_and_writes_are_never_allowed():
    data = manifest()
    writes = {
        "create",
        "apply",
        "delete",
        "patch",
        "scale",
        "rollout restart",
        "label",
        "annotate",
    }
    for profile in ("normal", "auto"):
        rules = data["permissions"][profile]
        heads = {
            pattern.split(":", 1)[0]: posture
            for posture, patterns in rules.items()
            for pattern in patterns
        }
        for path in parser_paths():
            assert any(path == head or path.startswith(head + " ") for head in heads), path
        for command in writes:
            assert heads[command] == "ask"


def _render(profiles: dict, mutable: bool = True) -> str:
    data = manifest()
    allowed = set(data["config_schema"]["properties"])
    stripped = {
        name: {key: value for key, value in profile.items() if key in allowed}
        for name, profile in profiles.items()
    }
    env = Environment(
        loader=FileSystemLoader(PKG),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(data["system_prompt"]).render(
        plugin_name="k8s",
        profiles=stripped,
        config_path=None,
        config_mutable=mutable,
    )


def test_prompt_configured_and_unconfigured_branches():
    text = _render({"prod": {"context": "", "namespace": "analytics", "allowed_namespaces": "analytics"}})
    assert "current-context" in text
    assert "analytics" in text

    text = _render({})
    assert "k8s-setup" in text
    text = _render({}, mutable=False)
    assert "k8s-setup" not in text
    assert "administrator" in text


def test_skills_and_setup_mutability_marker():
    skills = PKG / manifest()["skills"]
    assert (skills / "k8s" / "SKILL.md").is_file()
    setup = (skills / "k8s-setup" / "SKILL.md").read_text(encoding="utf-8")
    assert "requires_mutable_config: true" in setup


def test_package_never_imports_datus():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import datus_k8s_plugin.cli; "
            "assert not any(m == 'datus' or m.startswith('datus.') for m in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
