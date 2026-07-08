"""Turn the profile dict handed over by Datus into validated settings.

Datus resolves ``agent.plugins.airflow.<profile>`` from agent.yml, expands
``${VAR}`` references and passes the plain dict to the plugin constructor.
This module is the single place that interprets those keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import ConfigError

DEFAULT_TIMEOUT = 30.0
DEFAULT_CACHE_DIR = "~/.cache/datus-airflow-plugin"


def _normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    # Users often paste the full API root; the client appends /api/v2 itself.
    for suffix in ("/api/v2", "/api/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    if not url.startswith(("http://", "https://")):
        raise ConfigError(
            f"api_base_url must start with http:// or https:// (got {raw!r})"
        )
    return url


@dataclass
class S3Settings:
    """Optional overrides for the boto3 session used by `dags deploy`."""

    endpoint_url: Optional[str] = None
    region: Optional[str] = None
    profile: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    role_arn: Optional[str] = None
    role_session_name: Optional[str] = None
    external_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "S3Settings":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(
                f"unknown key(s) under plugins.airflow.<profile>.s3: {', '.join(sorted(unknown))}"
            )
        return cls(**{k: data.get(k) for k in known})


@dataclass
class Settings:
    profile_name: str = ""
    base_url: Optional[str] = None
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    auth_token_url: Optional[str] = None
    verify_ssl: Any = True  # True | False | path to a CA bundle
    timeout: float = DEFAULT_TIMEOUT
    dags_folder: Optional[str] = None
    s3: S3Settings = field(default_factory=S3Settings)
    cache_token: bool = True
    cache_dir: str = DEFAULT_CACHE_DIR

    @classmethod
    def from_profile(cls, profile: Optional[Dict[str, Any]]) -> "Settings":
        data = dict(profile or {})
        settings = cls()
        settings.profile_name = str(data.get("name", "") or "")

        raw_url = data.get("api_base_url") or data.get("base_url")
        if raw_url:
            settings.base_url = _normalize_base_url(str(raw_url))

        for key in ("token", "username", "password", "auth_token_url", "dags_folder"):
            value = data.get(key)
            if value is not None and str(value) != "":
                setattr(settings, key, str(value))

        if "verify_ssl" in data and data["verify_ssl"] is not None:
            settings.verify_ssl = data["verify_ssl"]

        if data.get("timeout") is not None:
            try:
                settings.timeout = float(data["timeout"])
            except (TypeError, ValueError):
                raise ConfigError(f"timeout must be a number (got {data['timeout']!r})")

        if data.get("cache_token") is not None:
            settings.cache_token = bool(data["cache_token"])
        if data.get("cache_dir"):
            settings.cache_dir = str(data["cache_dir"])

        s3_conf = data.get("s3")
        if s3_conf is not None:
            if not isinstance(s3_conf, dict):
                raise ConfigError("plugins.airflow.<profile>.s3 must be a mapping")
            settings.s3 = S3Settings.from_dict(s3_conf)

        return settings

    def require_base_url(self) -> str:
        if not self.base_url:
            raise ConfigError(
                "no api_base_url configured for this profile — add it under "
                "agent.plugins.airflow.<profile> in agent.yml (run the "
                "airflow-setup skill for a guided setup)"
            )
        return self.base_url

    def resolved_auth_token_url(self) -> str:
        if self.auth_token_url:
            return self.auth_token_url
        return f"{self.require_base_url()}/auth/token"

    def resolved_cache_dir(self) -> Path:
        return Path(self.cache_dir).expanduser()
