from __future__ import annotations

from types import SimpleNamespace

import yaml

from datus_aks_plugin.client import AksContext
from datus_aks_plugin.config import Settings


def test_cluster_connection_reads_user_kubeconfig():
    raw = b"""apiVersion: v1
clusters:
- cluster:
    server: https://aks.example
    certificate-authority-data: Q0E=
"""
    ctx = AksContext(
        Settings.from_profile(
            {"subscription_id": "s", "resource_group": "r", "cluster": "c"}
        )
    )
    ctx._client = SimpleNamespace(
        managed_clusters=SimpleNamespace(
            list_cluster_user_credentials=lambda group, cluster: SimpleNamespace(
                kubeconfigs=[SimpleNamespace(value=raw)]
            )
        )
    )
    value = ctx.cluster_connection().to_dict()
    assert value["provider"] == "aks"
    assert value["server"] == "https://aks.example"


def test_manifest_denies_credential():
    from pathlib import Path

    package = Path(__file__).parents[1] / "datus_aks_plugin"
    manifest = yaml.safe_load((package / "datus-plugin.yml").read_text())
    assert manifest["cli"] == "datus_aks_plugin.cli:main"
    assert "kubernetes credential:*" in manifest["permissions"]["normal"]["deny"]


def test_installed_aks_sdk_exposes_wrapped_operations():
    from azure.mgmt.containerservice import ContainerServiceClient

    class Credential:
        pass

    client = ContainerServiceClient(Credential(), "subscription")
    assert hasattr(client.managed_clusters, "list")
    assert hasattr(client.managed_clusters, "list_cluster_user_credentials")
    assert hasattr(client.agent_pools, "list")
    assert hasattr(client.maintenance_configurations, "list_by_managed_cluster")
