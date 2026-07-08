"""Turn the profile dict handed over by Datus into validated settings.

Datus resolves ``agent.plugins.statsig.<profile>`` from agent.yml, expands
``${VAR}`` references and passes the plain dict to the plugin constructor.
This module is the single place that interprets those keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .errors import ConfigError

DEFAULT_BASE_URL = "https://statsigapi.net"
DEFAULT_API_VERSION = "20240601"
DEFAULT_TIMEOUT = 30.0


def _normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    # Users often paste the full API root; the client appends /console/v1 itself.
    if url.endswith("/console/v1"):
        url = url[: -len("/console/v1")]
    if not url.startswith(("http://", "https://")):
        raise ConfigError(
            f"api_base_url must start with http:// or https:// (got {raw!r})"
        )
    return url


@dataclass
class Settings:
    profile_name: str = ""
    base_url: str = DEFAULT_BASE_URL
    api_key: Optional[str] = None
    api_version: str = DEFAULT_API_VERSION
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_profile(cls, profile: Optional[Dict[str, Any]]) -> "Settings":
        data = dict(profile or {})
        settings = cls()
        settings.profile_name = str(data.get("name", "") or "")

        raw_url = data.get("api_base_url") or data.get("base_url")
        if raw_url:
            settings.base_url = _normalize_base_url(str(raw_url))

        api_key = data.get("api_key") or data.get("token")
        if api_key is not None and str(api_key) != "":
            settings.api_key = str(api_key)

        if data.get("api_version"):
            settings.api_version = str(data["api_version"])

        if data.get("timeout") is not None:
            try:
                settings.timeout = float(data["timeout"])
            except (TypeError, ValueError):
                raise ConfigError(f"timeout must be a number (got {data['timeout']!r})")

        return settings

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "no api_key configured for this profile — add it under "
                "agent.plugins.statsig.<profile> in agent.yml as a ${ENV_VAR} "
                "reference (run the statsig-setup skill for a guided setup). "
                "Create a Console API Key at console.statsig.com/api_keys."
            )
        return self.api_key
