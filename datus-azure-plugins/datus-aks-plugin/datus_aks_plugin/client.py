from __future__ import annotations

import base64
from typing import Any

import yaml

from datus_azure_common import (
    ApiError,
    MissingDependencyError,
    build_credential,
    call,
    get_token,
)

from .cloud_contract import ClusterConnection, ExecCredential
from .config import Settings


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return (
        {key: val for key, val in vars(value).items() if not key.startswith("_")}
        if hasattr(value, "__dict__")
        else {"value": value}
    )


class AksContext:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._credential: Any = None
        self._client: Any = None

    @property
    def credential(self):
        if self._credential is None:
            self._credential = build_credential(self.settings.azure)
        return self._credential

    @property
    def client(self):
        if self._client is None:
            try:
                from azure.mgmt.containerservice import ContainerServiceClient
            except ImportError as exc:
                raise MissingDependencyError(
                    "azure-mgmt-containerservice is required for the AKS plugin"
                ) from exc
            base = self.settings.azure.resource_manager
            self._client = ContainerServiceClient(
                self.credential,
                self.settings.subscription_id,
                base_url=base,
                credential_scopes=[f"{base}/.default"],
            )
        return self._client

    def cluster(self):
        return call(
            self.client.managed_clusters.get,
            self.settings.resource_group,
            self.settings.cluster,
        )

    def _user_kubeconfig(self) -> dict[str, Any]:
        result = call(
            self.client.managed_clusters.list_cluster_user_credentials,
            self.settings.resource_group,
            self.settings.cluster,
        )
        configs = (
            getattr(result, "kubeconfigs", None)
            or (result.get("kubeconfigs") if isinstance(result, dict) else None)
            or []
        )
        if not configs:
            raise ApiError("AKS returned no user kubeconfig")
        raw = (
            configs[0].get("value")
            if isinstance(configs[0], dict)
            else getattr(configs[0], "value", None)
        )
        if isinstance(raw, str):
            try:
                raw = base64.b64decode(raw, validate=True)
            except ValueError:
                raw = raw.encode()
        if not isinstance(raw, (bytes, bytearray)):
            raise ApiError("AKS returned an unreadable user kubeconfig")
        try:
            data = yaml.safe_load(bytes(raw))
        except yaml.YAMLError as exc:
            raise ApiError(f"AKS returned invalid kubeconfig: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError("AKS returned a non-object kubeconfig")
        return data

    def cluster_connection(self) -> ClusterConnection:
        data = self._user_kubeconfig()
        clusters = data.get("clusters") or []
        if not clusters:
            raise ApiError("AKS kubeconfig contains no cluster")
        entry = clusters[0].get("cluster") or {}
        server = str(entry.get("server") or "")
        ca = str(entry.get("certificate-authority-data") or "")
        if self.settings.use_private_endpoint:
            cluster = self.cluster()
            private = str(getattr(cluster, "private_fqdn", "") or "")
            if private:
                server = f"https://{private}"
        return ClusterConnection("aks", self.settings.cluster, server, ca)

    def exec_credential(self) -> ExecCredential:
        token, expiry = get_token(self.credential, self.settings.token_scope)
        return ExecCredential(token, expiry)
