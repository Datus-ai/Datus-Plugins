from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .errors import ApiError


@dataclass(frozen=True)
class ClusterConnection:
    provider: str
    cluster: str
    server: str
    certificate_authority_data: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.server)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ApiError("ACK cluster endpoint must be an https URL")
        if not self.certificate_authority_data:
            raise ApiError("ACK cluster certificate authority data is missing")

    def to_dict(self) -> dict[str, str]:
        return {
            "apiVersion": "cloud-k8s.datus.ai/v1",
            "kind": "ClusterConnection",
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

    def __post_init__(self) -> None:
        has_token = bool(self.token)
        has_certificate = bool(self.client_certificate_data)
        has_key = bool(self.client_key_data)
        if has_certificate != has_key:
            raise ApiError("ACK client certificate and private key must be provided together")
        if has_token == has_certificate:
            raise ApiError(
                "ACK Kubernetes credential must contain exactly one of a bearer token "
                "or a client certificate/private key pair"
            )

    def to_dict(self) -> dict[str, Any]:
        expiry = self.expiration_timestamp.astimezone(timezone.utc)
        status = {
            "expirationTimestamp": expiry.isoformat().replace("+00:00", "Z"),
        }
        if self.token:
            status["token"] = self.token
        else:
            status["clientCertificateData"] = str(self.client_certificate_data)
            status["clientKeyData"] = str(self.client_key_data)
        return {
            "apiVersion": "client.authentication.k8s.io/v1",
            "kind": "ExecCredential",
            "status": status,
        }


@dataclass(frozen=True)
class KubernetesAccess:
    connection: ClusterConnection
    credential: ExecCredential

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": "cloud-k8s.datus.ai/v2",
            "kind": "KubernetesAccess",
            "connection": self.connection.to_dict(),
            "credential": self.credential.to_dict(),
        }
