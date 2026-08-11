from __future__ import annotations

from typing import Any

from datus_gcp_common import (
    ApiError,
    MissingDependencyError,
    build_credentials,
    call,
    refresh_token,
)

from .cloud_contract import ClusterConnection, ExecCredential
from .config import Settings


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(value._pb, preserving_proto_field_name=True)
    except (ImportError, AttributeError):
        return (
            {key: val for key, val in vars(value).items() if not key.startswith("_")}
            if hasattr(value, "__dict__")
            else {"value": value}
        )


def field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class GkeContext:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._credentials: Any = None
        self._client: Any = None

    @property
    def credentials(self):
        if self._credentials is None:
            self._credentials, _ = build_credentials(self.settings.gcp)
        return self._credentials

    @property
    def client(self):
        if self._client is None:
            try:
                from google.api_core.client_options import ClientOptions
                from google.cloud import container_v1
            except ImportError as exc:
                raise MissingDependencyError(
                    "google-cloud-container is required for the GKE plugin"
                ) from exc
            options = (
                ClientOptions(api_endpoint=self.settings.gcp.api_endpoint)
                if self.settings.gcp.api_endpoint
                else None
            )
            self._client = container_v1.ClusterManagerClient(
                credentials=self.credentials, client_options=options
            )
        return self._client

    def cluster(self):
        return call(self.client.get_cluster, name=self.settings.cluster_path)

    def _endpoint(self, cluster: Any) -> str:
        public = str(field(cluster, "endpoint", "") or "")
        private_cfg = field(cluster, "private_cluster_config", {}) or {}
        private = str(field(private_cfg, "private_endpoint", "") or "")
        control = field(cluster, "control_plane_endpoints_config", {}) or {}
        dns_cfg = field(control, "dns_endpoint_config", {}) or {}
        dns = str(field(dns_cfg, "endpoint", "") or "")
        mode = self.settings.endpoint_mode
        endpoint = (
            {
                "public": public,
                "private": private,
                "dns": dns,
            }.get(mode)
            or dns
            or public
            or private
        )
        if not endpoint:
            raise ApiError(f"GKE cluster has no endpoint for endpoint_mode={mode}")
        return endpoint if endpoint.startswith("https://") else f"https://{endpoint}"

    def cluster_connection(self) -> ClusterConnection:
        cluster = self.cluster()
        auth = field(cluster, "master_auth", {}) or {}
        ca = str(field(auth, "cluster_ca_certificate", "") or "")
        return ClusterConnection(
            "gke", self.settings.cluster, self._endpoint(cluster), ca
        )

    def exec_credential(self) -> ExecCredential:
        token, expiry = refresh_token(self.credentials)
        return ExecCredential(token, expiry)
