from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from datus_azure_common import ApiError


@dataclass(frozen=True)
class ClusterConnection:
    provider: str
    cluster: str
    server: str
    certificate_authority_data: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.server)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ApiError("AKS cluster endpoint must be an https URL")
        if not self.certificate_authority_data:
            raise ApiError("AKS cluster certificate authority data is missing")

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
    token: str
    expiration_timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        expiry = self.expiration_timestamp.astimezone(timezone.utc)
        return {
            "apiVersion": "client.authentication.k8s.io/v1",
            "kind": "ExecCredential",
            "status": {
                "expirationTimestamp": expiry.isoformat().replace("+00:00", "Z"),
                "token": self.token,
            },
        }
