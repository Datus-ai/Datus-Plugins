"""Turn the profile dict handed over by Datus into validated settings.

Datus resolves ``agent.plugins.airflow.<profile>`` from agent.yml, expands
``${VAR}`` references and passes the plain dict to the ``cli`` entry function
declared in ``datus-plugin.yml``. This module is the single place that
interprets those keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .errors import ConfigError

DEFAULT_TIMEOUT = 30.0
DEFAULT_CACHE_DIR = "~/.cache/datus-airflow-plugin"
API_VERSIONS = ("v1", "v2")

# Every top-level command group the CLI registers. `allow_commands` is validated
# against this tuple, so it lives here rather than in the cli package (which
# would import back into config). test_plugin_contract keeps it in sync with the
# real parser.
COMMAND_GROUPS = (
    "dags",
    "tasks",
    "variables",
    "connections",
    "pools",
    "assets",
    "backfill",
    "version",
    "health",
    "providers",
    "plugins",
    "config",
    "jobs",
)


def _normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    # Users often paste the full API root; the client appends the selected
    # /api/v1 or /api/v2 prefix itself.
    for suffix in ("/api/v2", "/api/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    if not url.startswith(("http://", "https://")):
        raise ConfigError(
            f"api_base_url must start with http:// or https:// (got {raw!r})"
        )
    return url


def _resolve_api_version(raw_url: str, raw_version: Any) -> str:
    """Resolve the REST API generation, preserving URL suffix compatibility."""
    if raw_version is None or str(raw_version).strip().lower() == "auto":
        normalized_url = raw_url.strip().rstrip("/").lower()
        if normalized_url.endswith("/api/v1"):
            return "v1"
        if normalized_url.endswith("/api/v2"):
            return "v2"
        return "v2"
    value = str(raw_version).strip().lower()
    value = {"1": "v1", "2": "v2"}.get(value, value)
    if value not in API_VERSIONS:
        raise ConfigError(
            f"api_version must be one of: auto, v1, v2 (got {raw_version!r})"
        )
    return value


def _parse_csv(raw: Any) -> Tuple[str, ...]:
    """Split a comma-separated string (or a YAML list) into clean entries."""
    if raw is None:
        return ()
    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return tuple(entry for entry in (str(item).strip() for item in items) if entry)


def _parse_allow_commands(raw: Any) -> Tuple[str, ...]:
    """Validate the command-group allowlist; empty means every group is available."""
    groups = _parse_csv(raw)
    for group in groups:
        if group in COMMAND_GROUPS:
            continue
        if " " in group:
            raise ConfigError(
                f"allow_commands only takes top-level command groups, not subcommands "
                f"(got {group!r} — use {group.split(' ', 1)[0]!r} to allow the whole group)"
            )
        raise ConfigError(
            f"unknown command group in allow_commands: {group!r} — "
            f"valid groups are: {', '.join(COMMAND_GROUPS)}"
        )
    return groups


@dataclass
class Settings:
    profile_name: str = ""
    base_url: Optional[str] = None
    api_version: str = "v2"
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    auth_token_url: Optional[str] = None
    verify_ssl: Any = True  # True | False | path to a CA bundle
    timeout: float = DEFAULT_TIMEOUT
    dags_folder: Optional[str] = None
    # Scope guardrails: empty means unrestricted. They keep the agent inside one
    # tenant's DAGs / command surface — they are not a security boundary (anyone
    # can edit agent.yml or call the REST API directly).
    dag_id_prefix: Tuple[str, ...] = ()
    allow_commands: Tuple[str, ...] = ()
    cache_token: bool = True
    cache_dir: str = DEFAULT_CACHE_DIR

    @classmethod
    def from_profile(cls, profile: Optional[Dict[str, Any]]) -> "Settings":
        data = dict(profile or {})
        if "s3" in data:
            raise ConfigError(
                "plugins.airflow.<profile>.s3 is no longer supported: configure "
                "agent.plugins.s3 separately and let the airflow-dag-export skill "
                "route uploads from the dags_folder URI"
            )
        settings = cls()
        settings.profile_name = str(data.get("name", "") or "")

        raw_url = data.get("api_base_url") or data.get("base_url")
        if raw_url:
            raw_url = str(raw_url)
            settings.api_version = _resolve_api_version(raw_url, data.get("api_version"))
            settings.base_url = _normalize_base_url(raw_url)
        elif data.get("api_version") is not None:
            settings.api_version = _resolve_api_version("", data.get("api_version"))

        for key in ("token", "username", "password", "auth_token_url", "dags_folder"):
            value = data.get(key)
            if value is not None and str(value) != "":
                setattr(settings, key, str(value))

        settings.dag_id_prefix = _parse_csv(data.get("dag_id_prefix"))
        settings.allow_commands = _parse_allow_commands(data.get("allow_commands"))

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

        return settings

    @property
    def scoped(self) -> bool:
        """True when this profile restricts which DAGs may be touched."""
        return bool(self.dag_id_prefix)

    def allowed_groups(self) -> Optional[set]:
        """The command groups this profile may use, or None when unrestricted."""
        return set(self.allow_commands) if self.allow_commands else None

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
