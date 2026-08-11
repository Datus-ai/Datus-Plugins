"""Shared plumbing for Datus Azure plugins."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_MISSING_DEPENDENCY = 8
FORMATS = ("table", "json", "yaml", "plain")
FRAMEWORK_KEYS = frozenset({"name", "default"})
AZURE_KEYS = frozenset(
    {
        "cloud",
        "tenant_id",
        "client_id",
        "client_secret",
        "managed_identity_client_id",
        "timeout",
        "max_attempts",
    }
)


class PluginError(Exception):
    exit_code = EXIT_RUNTIME


class UsageError(PluginError):
    exit_code = EXIT_USAGE


class ConfigError(PluginError):
    exit_code = EXIT_CONFIG


class MissingDependencyError(PluginError):
    exit_code = EXIT_MISSING_DEPENDENCY


class ApiError(PluginError):
    pass


def validate_keys(data: Mapping[str, Any], extra: Iterable[str], where: str) -> None:
    unknown = set(data) - AZURE_KEYS - FRAMEWORK_KEYS - set(extra)
    if unknown:
        raise ConfigError(f"unknown key(s) under {where}: {', '.join(sorted(unknown))}")


@dataclass(frozen=True)
class AzureSettings:
    cloud: str = "public"
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    managed_identity_client_id: str | None = None
    timeout: float = 60.0
    max_attempts: int = 3

    @classmethod
    def from_profile(cls, data: Mapping[str, Any]) -> "AzureSettings":
        cloud = str(data.get("cloud") or "public").lower()
        if cloud not in {"public", "china", "government"}:
            raise ConfigError("cloud must be public, china, or government")
        try:
            timeout = float(data.get("timeout") or 60)
            attempts = int(data.get("max_attempts") or 3)
        except (TypeError, ValueError) as exc:
            raise ConfigError("timeout and max_attempts must be numeric") from exc
        return cls(
            cloud=cloud,
            tenant_id=str(data.get("tenant_id") or "").strip() or None,
            client_id=str(data.get("client_id") or "").strip() or None,
            client_secret=str(data.get("client_secret") or "").strip() or None,
            managed_identity_client_id=str(
                data.get("managed_identity_client_id") or ""
            ).strip()
            or None,
            timeout=timeout,
            max_attempts=attempts,
        )

    @property
    def authority(self) -> str:
        return {
            "public": "https://login.microsoftonline.com",
            "china": "https://login.chinacloudapi.cn",
            "government": "https://login.microsoftonline.us",
        }[self.cloud]

    @property
    def resource_manager(self) -> str:
        return {
            "public": "https://management.azure.com",
            "china": "https://management.chinacloudapi.cn",
            "government": "https://management.usgovcloudapi.net",
        }[self.cloud]


def build_credential(settings: AzureSettings):
    try:
        from azure.identity import ClientSecretCredential, DefaultAzureCredential
    except ImportError as exc:
        raise MissingDependencyError(
            "azure-identity is required for Azure authentication"
        ) from exc
    if settings.client_secret:
        if not settings.tenant_id or not settings.client_id:
            raise ConfigError("tenant_id and client_id are required with client_secret")
        return ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            authority=settings.authority,
        )
    return DefaultAzureCredential(
        authority=settings.authority,
        managed_identity_client_id=settings.managed_identity_client_id,
        exclude_interactive_browser_credential=True,
    )


def get_token(credential: Any, scope: str) -> tuple[str, datetime]:
    try:
        token = credential.get_token(scope)
    except Exception as exc:
        raise ApiError(f"Azure credential acquisition failed: {exc}") from exc
    value = str(getattr(token, "token", "") or "")
    expires_on = int(getattr(token, "expires_on", 0) or 0)
    if not value or not expires_on:
        raise ApiError("Azure credential did not return a token with an expiry")
    return value, datetime.fromtimestamp(expires_on, timezone.utc)


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except PluginError:
        raise
    except Exception as exc:
        raise ApiError(f"Azure API error: {exc}") from exc


def add_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--output", choices=FORMATS, default="table")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def render_rows(
    rows: list[dict[str, Any]], columns: Sequence[str] | None, fmt: str
) -> str:
    if fmt == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False, default=str)
    if fmt == "yaml":
        import yaml

        return yaml.safe_dump(rows, sort_keys=False, allow_unicode=True)
    columns = list(columns or dict.fromkeys(key for row in rows for key in row))
    if fmt == "plain":
        return "\n".join(
            " ".join(_cell(row.get(key)) for key in columns) for row in rows
        )
    widths = [
        max([len(key), *[len(_cell(row.get(key))) for row in rows]]) for key in columns
    ]
    lines = [" | ".join(key.ljust(widths[i]) for i, key in enumerate(columns))]
    lines.append("=+=".join("=" * width for width in widths))
    lines.extend(
        " | ".join(
            _cell(row.get(key)).ljust(widths[i]) for i, key in enumerate(columns)
        ).rstrip()
        for row in rows
    )
    return "\n".join(lines)


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return (
        {key: val for key, val in vars(value).items() if not key.startswith("_")}
        if hasattr(value, "__dict__")
        else {"value": value}
    )


def render_one(value: Any, fmt: str) -> str:
    data = _dict(value)
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if fmt == "yaml":
        import yaml

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return render_rows(
        [{"property": key, "value": val} for key, val in data.items()],
        ["property", "value"],
        "plain" if fmt == "plain" else "table",
    )


def run(
    parser: argparse.ArgumentParser, argv: list[str], factory: Callable[[], Any]
) -> int:
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return int(ns.func(factory(), ns) or 0)
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
