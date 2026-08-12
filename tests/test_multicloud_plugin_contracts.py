from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).parents[1]
PLUGINS = (
    ("gke", ROOT / "datus-gcp-plugins/datus-gke-plugin", "datus_gke_plugin"),
    ("gcs", ROOT / "datus-gcp-plugins/datus-gcs-plugin", "datus_gcs_plugin"),
    ("aks", ROOT / "datus-azure-plugins/datus-aks-plugin", "datus_aks_plugin"),
    ("adls", ROOT / "datus-azure-plugins/datus-adls-plugin", "datus_adls_plugin"),
    ("ack", ROOT / "datus-aliyun-plugins/datus-ack-plugin", "datus_ack_plugin"),
)


def _requirement_name(requirement: str) -> str:
    """Normalized PEP 508 project name, ignoring extras, versions, and markers."""
    name = re.split(r"[\s\[<>=!~;(@]", str(requirement).strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _manifest(directory: Path, package: str) -> dict:
    return yaml.safe_load((directory / package / "datus-plugin.yml").read_text())


def _safe_profiles(manifest: dict) -> dict:
    values = {
        "project": "project",
        "location": "region",
        "cluster": "cluster",
        "subscription_id": "subscription",
        "resource_group": "group",
        "region_id": "cn-hangzhou",
        "cluster_id": "cluster-id",
        "account_url": "https://account.dfs.core.windows.net",
        "bucket": "bucket",
        "container": "container",
    }
    props = manifest["config_schema"]["properties"]
    profile = {
        key: value
        for key, value in values.items()
        if key in props and props[key].get("x-secret") is not True
    }
    return {"test": profile}


def _command_paths(commands: list[dict], prefix: tuple[str, ...] = ()):
    for command in commands:
        path = (*prefix, command["name"])
        subcommands = command.get("subcommands")
        if subcommands:
            yield from _command_paths(subcommands, path)
        else:
            yield path


def _skill_frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n")
    frontmatter, _body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(frontmatter)


def test_multicloud_distribution_contracts():
    for name, directory, package in PLUGINS:
        project = tomllib.loads((directory / "pyproject.toml").read_text())
        assert project["project"]["entry-points"]["datus.plugins"] == {name: package}
        dependencies = project["project"].get("dependencies", [])
        assert "datus" not in {_requirement_name(item) for item in dependencies}

        manifest = _manifest(directory, package)
        assert manifest["manifest_version"] == 1
        assert manifest["cli"].startswith(package + ".")
        assert (directory / package / manifest["skills"]).is_dir()
        assert (directory / package / manifest["system_prompt"]).is_file()
        assert manifest["commands"]
        assert set(manifest["permissions"]) == {"normal", "auto"}

        source = "\n".join(
            path.read_text() for path in (directory / package).rglob("*.py")
        )
        assert "import datus" not in source
        assert "from datus." not in source


def test_multicloud_main_skills_document_every_manifest_command():
    for name, directory, package in PLUGINS:
        manifest = _manifest(directory, package)
        skill = (directory / package / "skills" / name / "SKILL.md").read_text()
        missing = [
            " ".join(path)
            for path in _command_paths(manifest["commands"])
            if f"datus {name} {' '.join(path)}" not in skill
        ]
        assert not missing, f"{name} skill is missing commands: {missing}"


def test_multicloud_skills_have_valid_datus_metadata():
    for name, directory, package in PLUGINS:
        skills = directory / package / "skills"
        for skill_name in (name, f"{name}-setup"):
            path = skills / skill_name / "SKILL.md"
            metadata = _skill_frontmatter(path)
            assert metadata["name"] == skill_name
            assert isinstance(metadata["description"], str)
            assert metadata["description"].strip()
            assert set(metadata) <= {
                "name",
                "description",
                "requires_mutable_config",
            }
            assert len(path.read_text().splitlines()) < 500

        setup = _skill_frontmatter(skills / f"{name}-setup" / "SKILL.md")
        assert setup["requires_mutable_config"] is True


def test_multicloud_prompts_render_configured_and_unconfigured():
    for _name, directory, package in PLUGINS:
        manifest = _manifest(directory, package)
        env = Environment(
            loader=FileSystemLoader(directory / package),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template = env.get_template(manifest["system_prompt"])
        common = {"plugin_name": _name, "config_path": None}
        assert template.render(
            profiles=_safe_profiles(manifest), config_mutable=True, **common
        )
        mutable = template.render(profiles={}, config_mutable=True, **common)
        immutable = template.render(profiles={}, config_mutable=False, **common)
        assert f"{_name}-setup" in mutable
        assert f"{_name}-setup" not in immutable


def test_provider_credentials_are_never_agent_bash_visible():
    for name, directory, package in PLUGINS:
        if name not in {"gke", "aks", "ack"}:
            continue
        manifest = _manifest(directory, package)
        for profile in ("normal", "auto"):
            assert "kubernetes credential:*" in manifest["permissions"][profile]["deny"]


def test_common_libraries_do_not_register_plugins():
    for directory in (
        ROOT / "datus-gcp-plugins/datus-gcp-common",
        ROOT / "datus-azure-plugins/datus-azure-common",
    ):
        project = tomllib.loads((directory / "pyproject.toml").read_text())
        assert "entry-points" not in project["project"]
