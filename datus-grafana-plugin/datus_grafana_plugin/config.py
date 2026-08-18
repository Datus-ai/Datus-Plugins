from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConfigError


def _text(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _bool(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    value = str(value).lower().strip()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ConfigError(f"expected true/false, got {value!r}")


@dataclass(frozen=True)
class Settings:
    base_url: str
    auth_mode: str
    token: str | None
    username: str | None
    password: str | None
    org_id: str | None
    api_mode: str
    namespace: str
    verify_ssl: bool
    timeout: float
    default_datasource_uid: str | None
    profile_name: str | None

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "Settings":
        base_url = str(profile.get("api_base_url") or "").strip().rstrip("/")
        if not base_url:
            raise ConfigError("api_base_url is required; run the grafana-setup skill")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError("api_base_url must start with http:// or https://")
        auth_mode = str(profile.get("auth_mode") or "token").lower()
        if auth_mode not in {"token", "basic"}:
            raise ConfigError("auth_mode must be token or basic")
        token = _text(profile.get("token"))
        username, password = _text(profile.get("username")), _text(profile.get("password"))
        if auth_mode == "token" and not token:
            raise ConfigError("token auth_mode requires token")
        if auth_mode == "basic" and not (username and password):
            raise ConfigError("basic auth_mode requires username and password")
        api_mode = str(profile.get("api_mode") or "auto").lower()
        if api_mode not in {"auto", "new", "legacy"}:
            raise ConfigError("api_mode must be auto, new, or legacy")
        try:
            timeout = float(profile.get("timeout") or 30)
        except (TypeError, ValueError) as exc:
            raise ConfigError("timeout must be a positive number") from exc
        if timeout <= 0:
            raise ConfigError("timeout must be a positive number")
        return cls(
            base_url=base_url, auth_mode=auth_mode, token=token, username=username,
            password=password, org_id=_text(profile.get("org_id")), api_mode=api_mode,
            namespace=str(profile.get("namespace") or "default"),
            verify_ssl=_bool(profile.get("verify_ssl"), True), timeout=timeout,
            default_datasource_uid=_text(profile.get("default_datasource_uid")),
            profile_name=_text(profile.get("name")),
        )
