"""CLI plumbing shared by AWS plugins: a multi-client context, arg helpers, and
a dispatch loop that turns :class:`PluginError` into the right exit code.

``AwsContext`` lazily builds and caches a boto3 client per service (a single
plugin often needs two — e.g. ``glue`` + ``logs``). Tests inject fakes by
assigning ``ctx._clients[service] = fake``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from .errors import EXIT_USAGE, PluginError, UsageError
from .output import DEFAULT_FORMAT, FORMATS
from .session import build_client


class AwsContext:
    """Carries the plugin ``Settings`` (which must expose ``.aws: AwsSettings``)
    and lazily builds boto3 clients on demand."""

    def __init__(self, settings: Any):
        self.settings = settings
        self._clients: Dict[str, Any] = {}

    def client(self, service: str) -> Any:
        if service not in self._clients:
            self._clients[service] = build_client(self.settings.aws, service)
        return self._clients[service]


def summarize_aws_profile(cfg: Dict[str, Any], extra_fields=()) -> str:
    """One-line, secret-free summary of a profile for ``system_prompt``.

    Never emits access_key_id / secret_access_key / session_token; only the
    region, AWS named profile, role ARN, credential mode, and whitelisted
    plugin-specific fields.
    """
    cfg = cfg or {}
    parts = []
    if cfg.get("region"):
        parts.append(f"region={cfg['region']}")
    if cfg.get("profile"):
        parts.append(f"aws_profile={cfg['profile']}")
    if cfg.get("role_arn"):
        parts.append(f"role={cfg['role_arn']}")
    for field in extra_fields:
        if cfg.get(field):
            parts.append(f"{field}={cfg[field]}")
    if cfg.get("access_key_id"):
        creds = "keys"
    elif cfg.get("role_arn"):
        creds = "assume-role"
    else:
        creds = "chain"
    parts.append(f"creds={creds}")
    return ", ".join(parts)


def add_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        choices=FORMATS,
        default=DEFAULT_FORMAT,
        help="output format (default: table; json/yaml include all fields)",
    )


def parse_json_arg(raw: str, what: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise UsageError(f"{what} is not valid JSON: {exc}") from exc


def parse_datetime_arg(raw: str, what: str) -> datetime:
    """Accept ISO-8601 (date or datetime, Z ok) or 'now'; return an aware datetime
    (boto3 time parameters accept datetime objects directly)."""
    if raw.lower() == "now":
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsageError(f"{what} must be an ISO 8601 date/datetime (got {raw!r})") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise UsageError(f"{prompt} — refusing to proceed non-interactively; pass -y/--yes")
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def run(parser: argparse.ArgumentParser, argv: list, context_factory: Callable[[], Any]) -> int:
    """Parse argv, build the context, dispatch to ``ns.func`` and map errors.

    ``context_factory`` is called inside the try so a ConfigError from
    ``Settings.from_profile`` (e.g. an unknown key) surfaces as exit 3.
    """
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # -h or usage error; keep the CLI convention
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else EXIT_USAGE

    try:
        ctx = context_factory()
        rc = ns.func(ctx, ns)
        return 0 if rc is None else int(rc)
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
