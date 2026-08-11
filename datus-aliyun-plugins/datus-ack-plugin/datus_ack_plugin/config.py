from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConfigError

KNOWN_KEYS = {
    "name",
    "default",
    "region_id",
    "cluster_id",
    "access_key_id",
    "access_key_secret",
    "security_token",
    "role_arn",
    "role_session_name",
    "credentials_uri",
    "endpoint",
    "use_private_endpoint",
    "credential_ttl_minutes",
    "timeout",
    "max_attempts",
}


def _bool(value: Any) -> bool:
    text = str(value or "false").lower()
    if text not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigError("use_private_endpoint must be true or false")
    return text in {"true", "1", "yes"}


@dataclass(frozen=True)
class Settings:
    region_id: str
    cluster_id: str
    access_key_id: str | None = None
    access_key_secret: str | None = None
    security_token: str | None = None
    role_arn: str | None = None
    role_session_name: str = "datus-ack"
    credentials_uri: str | None = None
    endpoint: str | None = None
    use_private_endpoint: bool = False
    credential_ttl_minutes: int = 15
    timeout: float = 60.0
    max_attempts: int = 3

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "Settings":
        data = dict(profile or {})
        unknown = set(data) - KNOWN_KEYS
        if unknown:
            raise ConfigError(
                f"unknown key(s) under plugins.ack.<profile>: {', '.join(sorted(unknown))}"
            )
        region = str(data.get("region_id") or "").strip()
        cluster = str(data.get("cluster_id") or "").strip()
        if not region or not cluster:
            raise ConfigError(
                "region_id and cluster_id are required in the ACK profile"
            )
        key_id = str(data.get("access_key_id") or "").strip() or None
        key_secret = str(data.get("access_key_secret") or "").strip() or None
        if bool(key_id) != bool(key_secret):
            raise ConfigError(
                "access_key_id and access_key_secret must be configured together"
            )
        try:
            ttl = int(data.get("credential_ttl_minutes") or 15)
            timeout = float(data.get("timeout") or 60)
            attempts = int(data.get("max_attempts") or 3)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                "credential_ttl_minutes, timeout, and max_attempts must be numeric"
            ) from exc
        if ttl < 15:
            raise ConfigError("credential_ttl_minutes must be at least 15")
        return cls(
            region,
            cluster,
            key_id,
            key_secret,
            str(data.get("security_token") or "").strip() or None,
            str(data.get("role_arn") or "").strip() or None,
            str(data.get("role_session_name") or "datus-ack").strip(),
            str(data.get("credentials_uri") or "").strip() or None,
            str(data.get("endpoint") or "").strip() or None,
            _bool(data.get("use_private_endpoint")),
            ttl,
            timeout,
            attempts,
        )
