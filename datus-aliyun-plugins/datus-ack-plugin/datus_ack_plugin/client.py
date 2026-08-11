from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from .cloud_contract import ClusterConnection, ExecCredential
from .config import Settings
from .errors import ApiError, ConfigError, MissingDependencyError


def as_dict(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_map"):
        return value.to_map()
    if hasattr(value, "body"):
        return as_dict(value.body)
    return (
        {
            key: as_dict(val)
            for key, val in vars(value).items()
            if not key.startswith("_")
        }
        if hasattr(value, "__dict__")
        else value
    )


def _body(value: Any) -> Any:
    return as_dict(getattr(value, "body", value))


def _jwt_expiry(token: str, fallback: datetime) -> datetime:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(int(value["exp"]), timezone.utc)
    except (IndexError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


class AckContext:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any = None
        self._kubeconfig: dict[str, Any] | None = None

    @property
    def client(self):
        if self._client is None:
            try:
                from alibabacloud_credentials.client import Client as CredentialClient
                from alibabacloud_credentials.models import Config as CredentialConfig
                from alibabacloud_cs20151215.client import Client as CsClient
                from alibabacloud_tea_openapi.models import Config as OpenApiConfig
            except ImportError as exc:
                raise MissingDependencyError(
                    "Alibaba Cloud ACK SDK dependencies are unavailable"
                ) from exc
            cfg = CredentialConfig()
            if self.settings.credentials_uri:
                cfg.type = "credentials_uri"
                cfg.credentials_uri = self.settings.credentials_uri
            elif self.settings.role_arn:
                cfg.type = "ram_role_arn"
                cfg.access_key_id = self.settings.access_key_id
                cfg.access_key_secret = self.settings.access_key_secret
                cfg.role_arn = self.settings.role_arn
                cfg.role_session_name = self.settings.role_session_name
            elif self.settings.access_key_id:
                cfg.type = "sts" if self.settings.security_token else "access_key"
                cfg.access_key_id = self.settings.access_key_id
                cfg.access_key_secret = self.settings.access_key_secret
                cfg.security_token = self.settings.security_token
            credential = (
                CredentialClient(cfg)
                if getattr(cfg, "type", None)
                else CredentialClient()
            )
            open_cfg = OpenApiConfig(credential=credential)
            open_cfg.region_id = self.settings.region_id
            open_cfg.endpoint = (
                self.settings.endpoint or f"cs.{self.settings.region_id}.aliyuncs.com"
            )
            self._client = CsClient(open_cfg)
        return self._client

    def invoke(self, method: str, *args: Any) -> Any:
        fn = getattr(self.client, method, None)
        if fn is None:
            raise ApiError(f"installed ACK SDK does not expose {method}")
        try:
            return _body(fn(*args))
        except Exception as exc:
            raise ApiError(f"ACK API error: {exc}") from exc

    def request(self, class_name: str, **values: Any) -> Any:
        try:
            from alibabacloud_cs20151215 import models
        except ImportError as exc:
            raise MissingDependencyError(
                "Alibaba Cloud ACK SDK models are unavailable"
            ) from exc
        cls = getattr(models, class_name, None)
        if cls is None:
            raise ApiError(f"installed ACK SDK does not expose {class_name}")
        return cls(**values)

    def user_kubeconfig(self) -> dict[str, Any]:
        if self._kubeconfig is not None:
            return self._kubeconfig
        request = self.request(
            "DescribeClusterUserKubeconfigRequest",
            private_ip_address=self.settings.use_private_endpoint,
            temporary_duration_minutes=self.settings.credential_ttl_minutes,
        )
        response = self.invoke(
            "describe_cluster_user_kubeconfig", self.settings.cluster_id, request
        )
        raw = response.get("config") if isinstance(response, dict) else None
        if not raw:
            raise ApiError("ACK returned no user kubeconfig")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ApiError(f"ACK returned invalid kubeconfig: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError("ACK returned a non-object kubeconfig")
        self._kubeconfig = data
        return data

    def cluster_connection(self) -> ClusterConnection:
        data = self.user_kubeconfig()
        clusters = data.get("clusters") or []
        if not clusters:
            raise ApiError("ACK kubeconfig contains no cluster")
        cluster = clusters[0].get("cluster") or {}
        return ClusterConnection(
            "ack",
            self.settings.cluster_id,
            str(cluster.get("server") or ""),
            str(cluster.get("certificate-authority-data") or ""),
        )

    def exec_credential(self) -> ExecCredential:
        data = self.user_kubeconfig()
        users = data.get("users") or []
        user = users[0].get("user") if users else None
        token = str((user or {}).get("token") or "")
        if not token:
            raise ConfigError(
                "ACK returned a client-certificate kubeconfig; this version requires a temporary bearer token"
            )
        fallback = datetime.now(timezone.utc) + timedelta(
            minutes=self.settings.credential_ttl_minutes - 1
        )
        return ExecCredential(token, _jwt_expiry(token, fallback))
