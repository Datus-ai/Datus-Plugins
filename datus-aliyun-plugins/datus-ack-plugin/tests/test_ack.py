from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import yaml

from datus_ack_plugin.client import AckContext
from datus_ack_plugin.config import Settings


def token():
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "exp": int(
                        (datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()
                    )
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"x.{payload}.x"


def test_provider_contract_from_temporary_kubeconfig():
    ctx = AckContext(
        Settings.from_profile({"region_id": "cn-hangzhou", "cluster_id": "c1"})
    )
    ctx._kubeconfig = {
        "clusters": [
            {
                "cluster": {
                    "server": "https://ack.example",
                    "certificate-authority-data": "Q0E=",
                }
            }
        ],
        "users": [{"user": {"token": token()}}],
    }
    assert ctx.cluster_connection().provider == "ack"
    assert ctx.exec_credential().token.startswith("x.")


def test_manifest_denies_credential():
    from pathlib import Path

    package = Path(__file__).parents[1] / "datus_ack_plugin"
    manifest = yaml.safe_load((package / "datus-plugin.yml").read_text())
    assert "kubernetes credential:*" in manifest["permissions"]["normal"]["deny"]


def test_installed_ack_sdk_exposes_wrapped_operations():
    from alibabacloud_cs20151215 import models
    from alibabacloud_cs20151215.client import Client

    for name in (
        "describe_clusters_v1",
        "describe_cluster_detail",
        "describe_cluster_node_pools",
        "describe_cluster_node_pool_detail",
        "describe_cluster_addons_version",
        "describe_cluster_addons_upgrade_status",
        "describe_cluster_tasks",
        "describe_task_info",
        "describe_cluster_user_kubeconfig",
    ):
        assert hasattr(Client, name)
    for name in (
        "DescribeClusterNodePoolsRequest",
        "DescribeClusterAddonsUpgradeStatusRequest",
        "DescribeClusterTasksRequest",
        "DescribeClusterUserKubeconfigRequest",
    ):
        assert hasattr(models, name)
