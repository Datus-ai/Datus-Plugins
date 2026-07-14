"""Shared AWS credential/session settings extracted from a Datus profile.

Datus resolves ``agent.plugins.<name>.<profile>`` from agent.yml, expands
``${VAR}`` references, and passes the plain dict to the plugin. ``AwsSettings``
interprets the credential/region keys that every AWS plugin shares; each plugin
layers its own keys on top and calls :func:`validate_keys` so a typo in the
profile fails fast instead of being silently ignored.

Generalised from datus-airflow-plugin's ``S3Settings`` (same AssumeRole model).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from .errors import ConfigError

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_ATTEMPTS = 3

# Credential/session keys shared by every AWS plugin profile.
AWS_KEYS = frozenset(
    {
        "region",
        "profile",
        "access_key_id",
        "secret_access_key",
        "session_token",
        "role_arn",
        "role_session_name",
        "external_id",
        "endpoint_url",
        "timeout",
        "max_attempts",
    }
)

# Keys Datus itself injects into the profile dict; always allowed.
_FRAMEWORK_KEYS = frozenset({"name", "default"})


def validate_keys(data: Dict[str, Any], extra_keys: Iterable[str], where: str) -> None:
    """Reject unknown keys in a profile dict (AWS keys + framework keys + plugin keys)."""
    known = AWS_KEYS | _FRAMEWORK_KEYS | set(extra_keys)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown key(s) under {where}: {', '.join(sorted(unknown))}")


@dataclass
class AwsSettings:
    region: Optional[str] = None
    profile: Optional[str] = None  # AWS named profile in ~/.aws/config
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    role_arn: Optional[str] = None
    role_session_name: Optional[str] = None
    external_id: Optional[str] = None
    endpoint_url: Optional[str] = None
    timeout: float = DEFAULT_TIMEOUT
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    @classmethod
    def from_profile(cls, data: Optional[Dict[str, Any]]) -> "AwsSettings":
        data = dict(data or {})
        settings = cls()
        for key in (
            "region",
            "profile",
            "access_key_id",
            "secret_access_key",
            "session_token",
            "role_arn",
            "role_session_name",
            "external_id",
            "endpoint_url",
        ):
            value = data.get(key)
            if value is not None and str(value) != "":
                setattr(settings, key, str(value))

        if data.get("timeout") is not None:
            try:
                settings.timeout = float(data["timeout"])
            except (TypeError, ValueError):
                raise ConfigError(f"timeout must be a number (got {data['timeout']!r})")

        if data.get("max_attempts") is not None:
            try:
                settings.max_attempts = int(data["max_attempts"])
            except (TypeError, ValueError):
                raise ConfigError(f"max_attempts must be an integer (got {data['max_attempts']!r})")

        return settings
