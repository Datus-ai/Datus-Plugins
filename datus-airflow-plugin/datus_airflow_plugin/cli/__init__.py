"""CLI wiring: argument parser, dispatch, and helpers shared by commands.

Command groups mirror the Airflow CLI (dags / tasks / variables / connections
/ pools / providers / plugins / config / jobs / assets / backfill / version /
health), backed by the REST API v2 instead of local Airflow internals, plus
``dags deploy`` for shipping DAG files to S3 or a mounted dags folder.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from ..client import AirflowClient
from ..config import Settings
from ..errors import EXIT_USAGE, PluginError, UsageError
from ..output import DEFAULT_FORMAT, FORMATS

PROG = "datus airflow"


class Context:
    """Carries settings and a lazily-created API client into command handlers."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional[AirflowClient] = None

    @property
    def client(self) -> AirflowClient:
        if self._client is None:
            self._client = AirflowClient(self.settings)
        return self._client


# ------------------------------------------------------------------ helpers


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


def parse_datetime_arg(raw: str, what: str) -> str:
    """Accept ISO-8601 (date or datetime, Z ok) or 'now'; return an aware ISO string."""
    if raw.lower() == "now":
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise UsageError(f"{what} must be an ISO 8601 date/datetime (got {raw!r})") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise UsageError(f"{prompt} — refusing to proceed non-interactively; pass -y/--yes")
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def quote_path_part(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    from . import (
        assets_cmd,
        backfill_cmd,
        connections_cmd,
        dags_cmd,
        misc_cmd,
        pools_cmd,
        tasks_cmd,
        variables_cmd,
    )

    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Remote Apache Airflow CLI over the REST API v2 (Airflow 3.x). "
            "Command groups mirror the Airflow CLI; `dags deploy` additionally "
            "ships DAG files to S3 or a local dags folder."
        ),
        epilog="Examples: `datus airflow dags list`, `datus airflow dags trigger my_dag --wait`, "
        "`datus airflow dags deploy ./dags --dest s3://bucket/dags/`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    dags_cmd.register(sub)
    tasks_cmd.register(sub)
    variables_cmd.register(sub)
    connections_cmd.register(sub)
    pools_cmd.register(sub)
    assets_cmd.register(sub)
    backfill_cmd.register(sub)
    misc_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # -h or usage error; keep the CLI convention
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else EXIT_USAGE

    try:
        settings = Settings.from_profile(profile)
        ctx = Context(settings)
        rc = ns.func(ctx, ns)
        return 0 if rc is None else int(rc)
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except requests.exceptions.SSLError as exc:
        print(
            f"error: TLS verification failed: {exc}\n"
            "hint: set verify_ssl to a CA bundle path (or false) in the profile",
            file=sys.stderr,
        )
        return 1
    except requests.exceptions.ConnectionError as exc:
        print(f"error: cannot reach the Airflow API: {exc}", file=sys.stderr)
        return 1
    except requests.exceptions.Timeout:
        print(
            f"error: request timed out after {settings.timeout}s "
            "(raise `timeout` in the profile if the server is slow)",
            file=sys.stderr,
        )
        return 1
    except requests.RequestException as exc:
        print(f"error: request failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
