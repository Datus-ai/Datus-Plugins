"""EKS side of the versioned cloud-to-Kubernetes JSON protocol.

The k8s plugin implements and validates the same wire format independently.
There is deliberately no package dependency between plugins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from datus_aws_common import ApiError

CONNECTION_API_VERSION = "cloud-k8s.datus.ai/v1"
EXEC_API_VERSION = "client.authentication.k8s.io/v1"


@dataclass(frozen=True)
class ClusterConnection:
    provider: str
    cluster: str
    server: str
    certificate_authority_data: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.server)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ApiError("EKS cluster endpoint must be an https URL")
        if not self.certificate_authority_data:
            raise ApiError("EKS cluster certificate authority data is missing")

    def to_dict(self) -> dict[str, str]:
        return {
            "apiVersion": CONNECTION_API_VERSION,
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
            "apiVersion": EXEC_API_VERSION,
            "kind": "ExecCredential",
            "status": {
                "expirationTimestamp": expiry.isoformat().replace("+00:00", "Z"),
                "token": self.token,
            },
        }
