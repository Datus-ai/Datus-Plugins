"""Validate an already resolved Datus profile without importing its config broker."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import math
import re

from .errors import ConfigError


@dataclass(frozen=True)
class Settings:
    server_url: str
    # None: not configured (operator must pick a site via ``sites list`` or
    # ``--site``). "": the default site, chosen explicitly.
    site_content_url: str | None = None
    # "token": Personal Access Token (token_name/token_secret); "login":
    # username/password. Same vocabulary as the Superset plugin's auth_mode.
    auth_mode: str = "token"
    token_name: str = field(default="", repr=False)
    token_secret: str = field(default="", repr=False)
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    api_version: str = "auto"
    verify_ssl: bool = True
    ca_bundle: str | None = None
    timeout: float = 60
    profile_name: str | None = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> Settings:
        def value(key: str, default: Any = "") -> Any:
            result = profile.get(key, default)
            if isinstance(result, str) and "${" in result:
                raise ConfigError(f"{key} contains an unresolved environment reference")
            return result

        base = value("server_url")
        if not isinstance(base, str):
            raise ConfigError("server_url must be a URL")
        parts = urlsplit(base)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ConfigError(
                "server_url must be an HTTP(S) URL without credentials, query or fragment"
            )
        raw_auth = value("auth_mode", None)
        if raw_auth is None:
            raise ConfigError("auth_mode is required: login or token")
        auth = str(raw_auth).strip().lower()
        if auth not in {"login", "token"}:
            raise ConfigError("auth_mode must be login or token")
        keys = (
            ("token_name", "token_secret")
            if auth == "token"
            else ("username", "password")
        )
        for key in keys:
            if not isinstance(value(key), str) or not value(key).strip():
                raise ConfigError(f"{auth} auth_mode requires {key}")
        verify = value("verify_ssl", True)
        if isinstance(verify, str):
            if verify.lower() not in {"true", "false"}:
                raise ConfigError("verify_ssl must be true or false")
            verify = verify.lower() == "true"
        if not isinstance(verify, bool):
            raise ConfigError("verify_ssl must be a boolean")
        try:
            timeout = float(value("timeout", 60))
        except (TypeError, ValueError):
            raise ConfigError("timeout must be a positive number") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ConfigError("timeout must be a positive finite number")
        version = value("api_version", "auto")
        if not isinstance(version, str) or (
            version != "auto" and not re.fullmatch(r"\d+\.\d+", version)
        ):
            raise ConfigError("api_version must be auto or a REST version such as 3.21")
        ca = value("ca_bundle", None)
        if ca is not None and (not isinstance(ca, str) or not Path(ca).is_file()):
            raise ConfigError("ca_bundle must name an existing certificate file")
        site = value("site_content_url", None)
        if site is not None and not isinstance(site, str):
            raise ConfigError(
                "site_content_url must be a string (empty for the default site)"
            )
        return cls(
            server_url=base.rstrip("/"),
            site_content_url=site,
            auth_mode=auth,
            token_name=value("token_name"),
            token_secret=value("token_secret"),
            username=value("username"),
            password=value("password"),
            api_version=version,
            verify_ssl=verify,
            ca_bundle=ca,
            timeout=timeout,
            profile_name=profile.get("name"),
        )

    def with_site(self, site_content_url: str | None) -> Settings:
        """Return a copy targeting ``site_content_url`` when an override is given."""
        if site_content_url is None:
            return self
        return replace(self, site_content_url=site_content_url)

    def require_site(self) -> None:
        """Fail with guidance when a site-scoped command has no site to target."""
        if self.site_content_url is None:
            raise ConfigError(
                "site_content_url is not configured; run `datus tableau sites list` "
                "to discover sites, then pass --site CONTENT_URL or save it in the "
                "profile (use an empty value for the default site)"
            )
