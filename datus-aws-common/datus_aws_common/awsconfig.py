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


# Field specs (name/description/required/secret/default) for the credential and
# session keys every AWS plugin shares. This is the `config_schema()` source for
# the shared keys; each plugin appends its own field specs via `extra_fields`.
_AWS_FIELD_SPECS = (
    {"name": "region", "description": "AWS region, e.g. us-east-1 (falls back to the environment / ~/.aws)"},
    {"name": "profile", "description": "AWS named profile from ~/.aws/config to source credentials from"},
    {"name": "access_key_id", "description": "AWS access key ID (omit to use ambient credentials)"},
    {"name": "secret_access_key", "description": "AWS secret access key", "secret": True},
    {"name": "session_token", "description": "AWS session token for temporary credentials", "secret": True},
    {"name": "role_arn", "description": "IAM role ARN to assume before making calls"},
    {"name": "role_session_name", "description": "Session name to use when assuming role_arn"},
    {"name": "external_id", "description": "External ID for the assume-role trust policy"},
    {"name": "endpoint_url", "description": "Override the AWS service endpoint URL (e.g. a VPC endpoint / LocalStack)"},
    {"name": "timeout", "description": "Per-request client timeout in seconds", "default": DEFAULT_TIMEOUT},
    {"name": "max_attempts", "description": "Max retry attempts for AWS API calls", "default": DEFAULT_MAX_ATTEMPTS},
)


def aws_config_schema(extra_fields: Iterable[Dict[str, Any]] = ()) -> list:
    """Return ``config_schema()`` field specs: the shared AWS keys plus ``extra_fields``.

    Each returned spec carries ``name`` + ``description`` and optionally
    ``required`` / ``secret`` / ``default``; ``extra_fields`` (each a full spec)
    is appended after the shared AWS fields so a plugin's own keys keep their
    order and metadata.
    """
    return [dict(spec) for spec in _AWS_FIELD_SPECS] + [dict(spec) for spec in extra_fields]


def validate_aws_profile(
    profile: Optional[Dict[str, Any]],
    extra_keys: Iterable[str] = (),
    required: Iterable[str] = (),
) -> list:
    """Shape-check a candidate AWS profile before Datus saves it.

    Mirrors :meth:`AwsSettings.from_profile` / :func:`validate_keys` but never
    raises and never rejects a ``${ENV_VAR}`` placeholder — it sees the raw,
    un-expanded values, so placeholders are treated as opaque. Returns a list of
    error messages (empty = valid). Runtime validation stays in the constructor.
    """
    errors: list = []
    data = dict(profile or {})

    known = AWS_KEYS | _FRAMEWORK_KEYS | set(extra_keys)
    unknown = sorted(set(data) - known)
    if unknown:
        errors.append(f"unknown key(s): {', '.join(unknown)}")

    for key in required:
        value = data.get(key)
        if value is None or str(value).strip() == "":
            errors.append(f"{key} is required")

    for key in ("timeout", "max_attempts"):
        value = data.get(key)
        if value is None or str(value).strip() == "" or str(value).startswith("${"):
            continue
        try:
            float(value)
        except (TypeError, ValueError):
            errors.append(f"{key} must be a number (got {value!r})")

    endpoint = data.get("endpoint_url")
    if endpoint and not str(endpoint).startswith(("http://", "https://", "${")):
        errors.append("endpoint_url must start with http:// or https://")

    return errors
