"""Local validation for the managed-Kubernetes provider wire protocol.

This module is intentionally part of the k8s plugin. Cloud plugins implement
the same versioned JSON protocol independently; no plugin package depends on
another plugin or on a shared contract distribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

CONNECTION_API_VERSION = "cloud-k8s.datus.ai/v1"
CONNECTION_KIND = "ClusterConnection"
EXEC_API_VERSION = "client.authentication.k8s.io/v1"
EXEC_KIND = "ExecCredential"
ACCESS_API_VERSION = "cloud-k8s.datus.ai/v2"
ACCESS_KIND = "KubernetesAccess"


class ContractError(ValueError):
    pass


def _object(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{what} must be a JSON object")
    return value


def _text(data: Mapping[str, Any], key: str, what: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ContractError(f"{what}.{key} is required")
    return value


@dataclass(frozen=True)
class ClusterConnection:
    provider: str
    cluster: str
    server: str
    certificate_authority_data: str

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        expected_provider: str,
        expected_cluster: str | None = None,
    ) -> "ClusterConnection":
        data = _object(payload, "cluster connection")
        if data.get("apiVersion") != CONNECTION_API_VERSION:
            raise ContractError(
                f"unsupported cluster connection apiVersion: {data.get('apiVersion')!r}"
            )
        if data.get("kind") != CONNECTION_KIND:
            raise ContractError(f"unexpected cluster connection kind: {data.get('kind')!r}")
        provider = _text(data, "provider", "cluster connection")
        cluster = _text(data, "cluster", "cluster connection")
        server = _text(data, "server", "cluster connection")
        ca_data = _text(data, "certificateAuthorityData", "cluster connection")
        parsed = urlparse(server)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ContractError("cluster connection server must be an https URL")
        if provider != expected_provider:
            raise ContractError(f"provider returned {provider!r}, expected {expected_provider!r}")
        if expected_cluster is not None and cluster != expected_cluster:
            raise ContractError(
                f"provider returned cluster {cluster!r}, expected {expected_cluster!r}"
            )
        return cls(provider, cluster, server, ca_data)

    def to_dict(self) -> dict[str, str]:
        return {
            "apiVersion": CONNECTION_API_VERSION,
            "kind": CONNECTION_KIND,
            "provider": self.provider,
            "cluster": self.cluster,
            "server": self.server,
            "certificateAuthorityData": self.certificate_authority_data,
        }


@dataclass(frozen=True)
class ExecCredential:
    token: str | None
    expiration_timestamp: datetime
    client_certificate_data: str | None = None
    client_key_data: str | None = None

    @property
    def certificate_based(self) -> bool:
        return self.client_certificate_data is not None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecCredential":
        data = _object(payload, "exec credential")
        if data.get("apiVersion") != EXEC_API_VERSION:
            raise ContractError(
                f"unsupported exec credential apiVersion: {data.get('apiVersion')!r}"
            )
        if data.get("kind") != EXEC_KIND:
            raise ContractError(f"unexpected exec credential kind: {data.get('kind')!r}")
        status = _object(data.get("status"), "exec credential status")
        token = str(status.get("token") or "").strip() or None
        certificate = str(status.get("clientCertificateData") or "") or None
        private_key = str(status.get("clientKeyData") or "") or None
        if bool(certificate) != bool(private_key):
            raise ContractError(
                "exec credential clientCertificateData and clientKeyData must be provided together"
            )
        if bool(token) == bool(certificate):
            raise ContractError(
                "exec credential must contain exactly one of token or a client certificate/private key pair"
            )
        if certificate and (
            "-----BEGIN CERTIFICATE-----" not in certificate.strip()
            or "-----END CERTIFICATE-----" not in certificate.strip()
        ):
            raise ContractError("exec credential client certificate is not PEM encoded")
        if private_key and (
            "-----BEGIN " not in private_key.strip()
            or "PRIVATE KEY-----" not in private_key.strip()
        ):
            raise ContractError("exec credential client private key is not PEM encoded")
        raw_expiry = _text(status, "expirationTimestamp", "exec credential status")
        try:
            expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError(
                "exec credential status.expirationTimestamp must be ISO-8601"
            ) from exc
        if expiry.tzinfo is None:
            raise ContractError("exec credential expirationTimestamp must include a timezone")
        expiry = expiry.astimezone(timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise ContractError("exec credential is already expired")
        return cls(token, expiry, certificate, private_key)

    def to_dict(self) -> dict[str, Any]:
        expiry = self.expiration_timestamp.astimezone(timezone.utc)
        status: dict[str, str] = {
            "expirationTimestamp": expiry.isoformat().replace("+00:00", "Z"),
        }
        if self.token:
            status["token"] = self.token
        else:
            status["clientCertificateData"] = str(self.client_certificate_data)
            status["clientKeyData"] = str(self.client_key_data)
        return {
            "apiVersion": EXEC_API_VERSION,
            "kind": EXEC_KIND,
            "status": status,
        }


@dataclass(frozen=True)
class KubernetesAccess:
    connection: ClusterConnection
    credential: ExecCredential

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        expected_provider: str,
    ) -> "KubernetesAccess":
        data = _object(payload, "Kubernetes access")
        if data.get("apiVersion") != ACCESS_API_VERSION:
            raise ContractError(
                f"unsupported Kubernetes access apiVersion: {data.get('apiVersion')!r}"
            )
        if data.get("kind") != ACCESS_KIND:
            raise ContractError(f"unexpected Kubernetes access kind: {data.get('kind')!r}")
        return cls(
            ClusterConnection.from_dict(
                _object(data.get("connection"), "Kubernetes access connection"),
                expected_provider=expected_provider,
            ),
            ExecCredential.from_dict(
                _object(data.get("credential"), "Kubernetes access credential")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": ACCESS_API_VERSION,
            "kind": ACCESS_KIND,
            "connection": self.connection.to_dict(),
            "credential": self.credential.to_dict(),
        }
