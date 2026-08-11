"""Shared plumbing for Datus GCP plugins."""

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
GCP_KEYS = frozenset(
    {
        "project",
        "credentials_file",
        "impersonate_service_account",
        "quota_project",
        "scopes",
        "api_endpoint",
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
    unknown = set(data) - GCP_KEYS - FRAMEWORK_KEYS - set(extra)
    if unknown:
        raise ConfigError(f"unknown key(s) under {where}: {', '.join(sorted(unknown))}")


def _csv(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    return tuple(str(item).strip() for item in values if str(item).strip())


@dataclass(frozen=True)
class GcpSettings:
    project: str
    credentials_file: str | None = None
    impersonate_service_account: str | None = None
    quota_project: str | None = None
    scopes: tuple[str, ...] = ("https://www.googleapis.com/auth/cloud-platform",)
    api_endpoint: str | None = None
    timeout: float = 60.0
    max_attempts: int = 3

    @classmethod
    def from_profile(
        cls, data: Mapping[str, Any], *, require_project: bool = True
    ) -> "GcpSettings":
        project = str(data.get("project") or "").strip()
        if require_project and not project:
            raise ConfigError("project is required in the GCP plugin profile")
        try:
            timeout = float(data.get("timeout") or 60)
            attempts = int(data.get("max_attempts") or 3)
        except (TypeError, ValueError) as exc:
            raise ConfigError("timeout and max_attempts must be numeric") from exc
        scopes = _csv(data.get("scopes")) or (
            "https://www.googleapis.com/auth/cloud-platform",
        )
        return cls(
            project=project,
            credentials_file=str(data.get("credentials_file") or "").strip() or None,
            impersonate_service_account=str(
                data.get("impersonate_service_account") or ""
            ).strip()
            or None,
            quota_project=str(data.get("quota_project") or "").strip() or None,
            scopes=scopes,
            api_endpoint=str(data.get("api_endpoint") or "").strip() or None,
            timeout=timeout,
            max_attempts=attempts,
        )


def build_credentials(settings: GcpSettings):
    try:
        import google.auth
        from google.auth import impersonated_credentials
    except ImportError as exc:
        raise MissingDependencyError(
            "google-auth is required for GCP authentication"
        ) from exc
    kwargs = {
        "scopes": list(settings.scopes),
        "quota_project_id": settings.quota_project,
    }
    if settings.credentials_file:
        credentials, detected_project = google.auth.load_credentials_from_file(
            settings.credentials_file, **kwargs
        )
    else:
        credentials, detected_project = google.auth.default(**kwargs)
    if settings.impersonate_service_account:
        credentials = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=settings.impersonate_service_account,
            target_scopes=list(settings.scopes),
            lifetime=3600,
            quota_project_id=settings.quota_project,
        )
    return credentials, settings.project or str(detected_project or "")


def refresh_token(credentials: Any) -> tuple[str, datetime]:
    try:
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise MissingDependencyError(
            "google-auth requests transport is unavailable"
        ) from exc
    try:
        credentials.refresh(Request())
    except Exception as exc:
        raise ApiError(f"GCP credential refresh failed: {exc}") from exc
    token = str(getattr(credentials, "token", "") or "")
    expiry = getattr(credentials, "expiry", None)
    if not token or not isinstance(expiry, datetime):
        raise ApiError("GCP credentials did not return a token with an expiry")
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return token, expiry.astimezone(timezone.utc)


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except PluginError:
        raise
    except Exception as exc:
        raise ApiError(f"GCP API error: {exc}") from exc


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


def render_one(value: Any, fmt: str) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, dict):
        value = {"value": value}
    if fmt == "json":
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    if fmt == "yaml":
        import yaml

        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
    return render_rows(
        [{"property": key, "value": val} for key, val in value.items()],
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
