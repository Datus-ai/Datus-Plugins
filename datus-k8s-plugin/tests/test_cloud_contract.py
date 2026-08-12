from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from datus_k8s_plugin.cloud_contract import (
    ClusterConnection,
    ContractError,
    ExecCredential,
    KubernetesAccess,
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


def test_credential_contract_accepts_client_certificate_pair():
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    payload = credential_payload(future)
    payload["status"].pop("token")
    payload["status"].update(
        {
            "clientCertificateData": (
                "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----"
            ),
            "clientKeyData": (
                "-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----"
            ),
        }
    )
    credential = ExecCredential.from_dict(payload)
    assert credential.token is None
    assert credential.certificate_based is True
    assert credential.to_dict()["status"]["clientKeyData"].startswith("-----BEGIN")


@pytest.mark.parametrize(
    "status",
    [
        {},
        {"token": "secret", "clientCertificateData": "cert", "clientKeyData": "key"},
        {"clientCertificateData": "cert"},
    ],
)
def test_credential_contract_rejects_missing_mixed_or_incomplete_auth(status):
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    payload = credential_payload(future)
    payload["status"] = {"expirationTimestamp": future, **status}
    with pytest.raises(ContractError):
        ExecCredential.from_dict(payload)


def test_kubernetes_access_contract_combines_connection_and_credential():
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    payload = {
        "apiVersion": "cloud-k8s.datus.ai/v2",
        "kind": "KubernetesAccess",
        "connection": connection_payload(provider="ack"),
        "credential": credential_payload(future),
    }
    access = KubernetesAccess.from_dict(payload, expected_provider="ack")
    assert access.connection.cluster == "orders"
    assert access.credential.token == "secret"
