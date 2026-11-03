"""Validate an already resolved Datus profile without importing its config broker."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import math
import re

from .errors import ConfigError


@dataclass(frozen=True)
class Settings:
    server_url: str
    site_content_url: str = ""
    auth_type: str = "pat"
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
        auth = value("auth_type", "pat")
        if auth not in {"pat", "password"}:
            raise ConfigError("auth_type must be pat or password")
        keys = (
            ("token_name", "token_secret")
            if auth == "pat"
            else ("username", "password")
        )
        for key in keys:
            if not isinstance(value(key), str) or not value(key).strip():
                raise ConfigError(f"{key} is required for {auth} authentication")
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
        site = value("site_content_url", "")
        if not isinstance(site, str):
            raise ConfigError(
                "site_content_url must be a string (empty for the default site)"
            )
        return cls(
            server_url=base.rstrip("/"),
            site_content_url=site,
            auth_type=auth,
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
