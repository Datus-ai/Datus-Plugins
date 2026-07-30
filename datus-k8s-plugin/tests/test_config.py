from __future__ import annotations

from pathlib import Path

import pytest

from datus_k8s_plugin.config import Settings
from datus_k8s_plugin.errors import ConfigError, UsageError


def profile(**overrides):
    data = {
        "name": "test",
        "kubeconfig": "./conf/kubeconfig.yaml",
        "namespace": "analytics",
        "allowed_namespaces": "analytics, analytics-staging",
    }
    data.update(overrides)
    return data


def make_config(project: Path) -> Path:
    path = project / "conf" / "kubeconfig.yaml"
    path.parent.mkdir()
    path.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    return path


def test_relative_kubeconfig_resolves_from_project_directory(tmp_path):
    expected = make_config(tmp_path)
    settings = Settings.from_profile(profile())
    assert settings.resolve_kubeconfig(tmp_path) == expected
    assert settings.context is None


def test_absolute_kubeconfig_is_allowed(tmp_path):
    expected = make_config(tmp_path)
    settings = Settings.from_profile(profile(kubeconfig=str(expected)))
    assert settings.resolve_kubeconfig(tmp_path / "elsewhere") == expected


def test_relative_kubeconfig_cannot_escape_project(tmp_path):
    outside = tmp_path.parent / "outside-kubeconfig"
    outside.write_text("apiVersion: v1\n", encoding="utf-8")
    settings = Settings.from_profile(profile(kubeconfig="../outside-kubeconfig"))
    with pytest.raises(ConfigError, match="escapes"):
        settings.resolve_kubeconfig(tmp_path)


def test_relative_symlink_cannot_escape_project(tmp_path):
    outside = tmp_path.parent / "outside-kubeconfig-symlink-target"
    outside.write_text("apiVersion: v1\n", encoding="utf-8")
    link = tmp_path / "kubeconfig"
    link.symlink_to(outside)
    settings = Settings.from_profile(profile(kubeconfig="./kubeconfig"))
    with pytest.raises(ConfigError, match="escapes"):
        settings.resolve_kubeconfig(tmp_path)


def test_namespace_allowlist_is_enforced():
    settings = Settings.from_profile(profile())
    assert settings.check_namespace(None) == "analytics"
    assert settings.check_namespace("analytics-staging") == "analytics-staging"
    with pytest.raises(UsageError, match="outside"):
        settings.check_namespace("default")


def test_default_namespace_must_be_allowed():
    with pytest.raises(ConfigError, match="not present"):
        Settings.from_profile(profile(allowed_namespaces="analytics-staging"))


def test_unknown_profile_field_is_rejected():
    with pytest.raises(ConfigError, match="bogus"):
        Settings.from_profile(profile(bogus=True))


def test_missing_kubeconfig_is_config_error():
    with pytest.raises(ConfigError, match="required"):
        Settings.from_profile(
            {"namespace": "default", "allowed_namespaces": "default"}
        )
