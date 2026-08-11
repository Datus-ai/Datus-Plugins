from __future__ import annotations

from types import SimpleNamespace

import yaml

from datus_gke_plugin.client import GkeContext
from datus_gke_plugin.config import Settings


def test_cluster_connection_contract():
    ctx = GkeContext(
        Settings.from_profile({"project": "p", "location": "l", "cluster": "c"})
    )
    ctx._client = SimpleNamespace(
        get_cluster=lambda name: {
            "endpoint": "1.2.3.4",
            "master_auth": {"cluster_ca_certificate": "Q0E="},
        }
    )
    value = ctx.cluster_connection().to_dict()
    assert value["provider"] == "gke"
    assert value["server"] == "https://1.2.3.4"


def test_manifest_contract():
    from pathlib import Path

    package = Path(__file__).parents[1] / "datus_gke_plugin"
    manifest = yaml.safe_load((package / "datus-plugin.yml").read_text())
    assert manifest["manifest_version"] == 1
    assert manifest["cli"] == "datus_gke_plugin.cli:main"
    assert "kubernetes credential:*" in manifest["permissions"]["normal"]["deny"]


def test_installed_container_sdk_exposes_wrapped_methods():
    from google.cloud.container_v1 import ClusterManagerClient

    for name in (
        "list_clusters",
        "get_cluster",
        "list_node_pools",
        "get_node_pool",
        "list_operations",
        "get_operation",
        "get_server_config",
    ):
        assert hasattr(ClusterManagerClient, name)
