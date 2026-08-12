"""Interpret a resolved ``agent.plugins.superset.<profile>`` dictionary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConfigError


def _bool(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ConfigError(f"expected true/false, got {value!r}")


@dataclass(frozen=True)
class Settings:
    base_url: str
    auth_mode: str
    username: str | None
    password: str | None
    access_token: str | None
    provider: str
    verify_ssl: bool
    timeout: float
    serving_datasource: str | None
    serving_database_name: str | None
    profile_name: str | None

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "Settings":
        base_url = str(profile.get("api_base_url") or "").strip().rstrip("/")
        if not base_url:
            raise ConfigError("api_base_url is required; run the superset-setup skill")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError("api_base_url must start with http:// or https://")
        auth_mode = str(profile.get("auth_mode") or "login").strip().lower()
        if auth_mode not in {"login", "token"}:
            raise ConfigError("auth_mode must be login or token")
        username = _text(profile.get("username"))
        password = _text(profile.get("password"))
        access_token = _text(profile.get("access_token"))
        if auth_mode == "login" and not (username and password):
            raise ConfigError("login auth_mode requires username and password")
        if auth_mode == "token" and not access_token:
            raise ConfigError("token auth_mode requires access_token")
        try:
            timeout = float(profile.get("timeout") or 30)
        except (TypeError, ValueError) as exc:
            raise ConfigError("timeout must be a positive number") from exc
        if timeout <= 0:
            raise ConfigError("timeout must be a positive number")
        return cls(
            base_url=base_url,
            auth_mode=auth_mode,
            username=username,
            password=password,
            access_token=access_token,
            provider=str(profile.get("provider") or "db"),
            verify_ssl=_bool(profile.get("verify_ssl"), True),
            timeout=timeout,
            serving_datasource=_text(profile.get("serving_datasource")),
            serving_database_name=_text(profile.get("serving_database_name")),
            profile_name=_text(profile.get("name")),
        )


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
