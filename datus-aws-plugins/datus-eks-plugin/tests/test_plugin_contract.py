from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

PKG = Path(__file__).parent.parent / "datus_eks_plugin"


def test_manifest_and_assets_are_valid():
    manifest = yaml.safe_load((PKG / "datus-plugin.yml").read_text())
    assert manifest["manifest_version"] == 1
    assert manifest["cli"] == "datus_eks_plugin.cli:main"
    assert (PKG / manifest["skills"]).is_dir()
    assert (PKG / manifest["system_prompt"]).is_file()
    assert "kubernetes credential:*" in manifest["permissions"]["normal"]["deny"]
    assert "kubernetes credential:*" in manifest["permissions"]["auto"]["deny"]
    schema = manifest["config_schema"]["properties"]
    for key in ("access_key_id", "secret_access_key", "session_token", "external_id"):
        assert schema[key]["x-secret"] is True


def test_prompt_renders_configured_and_unconfigured():
    env = Environment(
        loader=FileSystemLoader(PKG),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("prompts/system.md.j2")
    common = {"plugin_name": "eks", "config_path": None}
    text = template.render(
        profiles={"dev": {"cluster": "dev", "region": "us-east-1"}},
        config_mutable=True,
        **common,
    )
    assert "cluster=dev" in text
    assert "secret" not in text
    assert "eks-setup" in template.render(
        profiles={}, config_mutable=True, **common
    )
    assert "eks-setup" not in template.render(
        profiles={}, config_mutable=False, **common
    )
