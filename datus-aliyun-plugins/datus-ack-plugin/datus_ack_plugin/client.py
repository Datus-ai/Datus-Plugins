from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from .cloud_contract import ClusterConnection, ExecCredential, KubernetesAccess
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


def _api_expiry(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _pem_from_kubeconfig(value: Any, *, kind: str) -> str:
    encoded = str(value or "").strip()
    if not encoded:
        raise ConfigError(f"ACK kubeconfig is missing client {kind} data")
    try:
        decoded = base64.b64decode("".join(encoded.split()), validate=True).decode(
            "utf-8"
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigError(f"ACK kubeconfig contains invalid client {kind} data") from exc
    markers = (
        ("-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----")
        if kind == "certificate"
        else (
            ("-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"),
            ("-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----"),
            ("-----BEGIN EC PRIVATE KEY-----", "-----END EC PRIVATE KEY-----"),
        )
    )
    pairs = (markers,) if kind == "certificate" else markers
    if not any(begin in decoded and end in decoded for begin, end in pairs):
        raise ConfigError(f"ACK kubeconfig client {kind} data is not PEM encoded")
    return decoded.strip() + "\n"


class AckContext:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any = None
        self._kubeconfig: dict[str, Any] | None = None
        self._expiration: datetime | None = None

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
            open_cfg.connect_timeout = self._timeout_ms
            open_cfg.read_timeout = self._timeout_ms
            self._client = CsClient(open_cfg)
        return self._client

    @property
    def _timeout_ms(self) -> int:
        return int(self.settings.timeout * 1000)

    def _runtime(self) -> Any:
        try:
            from alibabacloud_tea_util.models import RuntimeOptions
        except ImportError as exc:
            raise MissingDependencyError(
                "Alibaba Cloud ACK SDK dependencies are unavailable"
            ) from exc
        return RuntimeOptions(
            autoretry=True,
            max_attempts=self.settings.max_attempts,
            connect_timeout=self._timeout_ms,
            read_timeout=self._timeout_ms,
        )

    def invoke(self, method: str, *args: Any) -> Any:
        # The SDK exposes a *_with_options twin that accepts headers and
        # RuntimeOptions; `describe_clusters_v1` spells it without the
        # separating underscore.
        for name in (f"{method}_with_options", f"{method}with_options"):
            fn = getattr(self.client, name, None)
            if fn is not None:
                return self._call(fn, *args, {}, self._runtime())
        fn = getattr(self.client, method, None)
        if fn is None:
            raise ApiError(f"installed ACK SDK does not expose {method}")
        return self._call(fn, *args)

    def _call(self, fn: Any, *args: Any) -> Any:
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
        self._expiration = _api_expiry(response.get("expiration"))
        return data

    def cluster_connection(self) -> ClusterConnection:
        data = self.user_kubeconfig()
        cluster, _user = self._active_cluster_and_user(data)
        if not cluster:
            raise ApiError("ACK kubeconfig contains no cluster")
        return ClusterConnection(
            "ack",
            self.settings.cluster_id,
            str(cluster.get("server") or ""),
            str(cluster.get("certificate-authority-data") or ""),
        )

    def exec_credential(self) -> ExecCredential:
        data = self.user_kubeconfig()
        _cluster, user = self._active_cluster_and_user(data)
        if not user:
            raise ConfigError("ACK kubeconfig contains no user credential")
        fallback = datetime.now(timezone.utc) + timedelta(
            minutes=self.settings.credential_ttl_minutes - 1
        )
        token = str(user.get("token") or "").strip()
        expiry = _jwt_expiry(token, fallback) if token else fallback
        if self._expiration is not None:
            expiry = min(expiry, self._expiration)
        if token:
            return ExecCredential(token, expiry)
        certificate = _pem_from_kubeconfig(
            user.get("client-certificate-data"), kind="certificate"
        )
        private_key = _pem_from_kubeconfig(user.get("client-key-data"), kind="key")
        return ExecCredential(None, expiry, certificate, private_key)

    def kubernetes_access(self) -> KubernetesAccess:
        return KubernetesAccess(self.cluster_connection(), self.exec_credential())

    @staticmethod
    def _active_cluster_and_user(
        data: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        clusters = data.get("clusters") or []
        users = data.get("users") or []
        contexts = data.get("contexts") or []
        current = str(data.get("current-context") or "")
        selected = next(
            (item.get("context") or {} for item in contexts if item.get("name") == current),
            {},
        )

        def named(items: list[Any], name: Any, field: str) -> dict[str, Any]:
            if name:
                for item in items:
                    if isinstance(item, dict) and item.get("name") == name:
                        value = item.get(field) or {}
                        return value if isinstance(value, dict) else {}
            if items and isinstance(items[0], dict):
                value = items[0].get(field) or {}
                return value if isinstance(value, dict) else {}
            return {}

        return (
            named(clusters, selected.get("cluster"), "cluster"),
            named(users, selected.get("user"), "user"),
        )
