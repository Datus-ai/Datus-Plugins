from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from datus_k8s_plugin.cloud_contract import (
    ClusterConnection,
    ContractError,
    ExecCredential,
)


def connection_payload(**overrides):
    payload = {
        "apiVersion": "cloud-k8s.datus.ai/v1",
        "kind": "ClusterConnection",
        "provider": "aws",
        "cluster": "orders",
        "server": "https://cluster.example",
        "certificateAuthorityData": "Q0E=",
    }
    payload.update(overrides)
    return payload


def credential_payload(expiry):
    return {
        "apiVersion": "client.authentication.k8s.io/v1",
        "kind": "ExecCredential",
        "status": {"token": "secret", "expirationTimestamp": expiry},
    }


def test_connection_contract_validates_provider_cluster_and_tls():
    loaded = ClusterConnection.from_dict(
        connection_payload(), expected_provider="aws", expected_cluster="orders"
    )
    assert loaded.server == "https://cluster.example"
    with pytest.raises(ContractError, match="https"):
        ClusterConnection.from_dict(
            connection_payload(server="http://cluster.example"),
            expected_provider="aws",
            expected_cluster="orders",
        )


def test_connection_contract_can_accept_provider_owned_cluster_name():
    loaded = ClusterConnection.from_dict(
        connection_payload(cluster="provider-owned"), expected_provider="aws"
    )
    assert loaded.cluster == "provider-owned"
    with pytest.raises(ContractError, match="expected 'orders'"):
        ClusterConnection.from_dict(
            connection_payload(cluster="another"),
            expected_provider="aws",
            expected_cluster="orders",
        )


def test_credential_contract_rejects_expired_tokens():
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert ExecCredential.from_dict(credential_payload(future)).token == "secret"
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ContractError, match="expired"):
        ExecCredential.from_dict(credential_payload(expired))
