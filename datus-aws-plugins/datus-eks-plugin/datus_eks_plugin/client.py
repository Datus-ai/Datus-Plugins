"""EKS clients, cluster discovery, and IAM authenticator token generation."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any

from datus_aws_common import (
    ApiError,
    ConfigError,
    build_client,
    build_session,
    call,
)

from .cloud_contract import ClusterConnection, ExecCredential
from .config import Settings

TOKEN_PREFIX = "k8s-aws-v1."
PRESIGN_TTL_SECONDS = 60
TOKEN_TTL_MINUTES = 14


class EksContext:
    """Lazily share one AWS session across EKS and STS calls."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._session: Any = None
        self._clients: dict[str, Any] = {}

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = build_session(self.settings.aws)
        return self._session

    def client(self, service: str) -> Any:
        if service not in self._clients:
            kwargs = {} if service == "eks" else {"endpoint_url": None}
            self._clients[service] = build_client(
                self.settings.aws,
                service,
                session=self.session,
                **kwargs,
            )
        return self._clients[service]

    @property
    def region(self) -> str:
        value = self.settings.aws.region or getattr(self.session, "region_name", None)
        if not value:
            raise ConfigError(
                "AWS region is required for EKS; configure region in the EKS profile"
            )
        return str(value)

    def cluster_connection(self) -> ClusterConnection:
        response = call(
            self.client("eks").describe_cluster,
            name=self.settings.cluster,
        )
        cluster = response.get("cluster") or {}
        status = str(cluster.get("status") or "")
        if status and status not in {"ACTIVE", "UPDATING"}:
            raise ApiError(
                f"EKS cluster {self.settings.cluster!r} is not usable (status={status})"
            )
        endpoint = str(cluster.get("endpoint") or "")
        ca_data = str((cluster.get("certificateAuthority") or {}).get("data") or "")
        return ClusterConnection("eks", self.settings.cluster, endpoint, ca_data)

    def exec_credential(self) -> ExecCredential:
        """Generate the same EKS bearer token as `aws eks get-token`."""
        try:
            from botocore.signers import RequestSigner
        except ImportError as exc:  # pragma: no cover - common depends on botocore
            raise ConfigError("botocore is required for EKS authentication") from exc

        credentials = self.session.get_credentials()
        if credentials is None:
            raise ConfigError("AWS credentials are unavailable for EKS authentication")
        sts = self.client("sts")
        signer = RequestSigner(
            sts.meta.service_model.service_id,
            self.region,
            "sts",
            "v4",
            credentials,
            self.session.events,
        )
        request = {
            "method": "GET",
            "url": (
                f"{sts.meta.endpoint_url.rstrip('/')}"
                "/?Action=GetCallerIdentity&Version=2011-06-15"
            ),
            "body": {},
            "headers": {"x-k8s-aws-id": self.settings.cluster},
            "context": {},
        }
        signed_url = signer.generate_presigned_url(
            request,
            region_name=self.region,
            expires_in=PRESIGN_TTL_SECONDS,
            operation_name="",
        )
        encoded = base64.urlsafe_b64encode(signed_url.encode("utf-8")).decode("ascii")
        token = TOKEN_PREFIX + encoded.rstrip("=")
        return ExecCredential(
            token,
            datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES),
        )
