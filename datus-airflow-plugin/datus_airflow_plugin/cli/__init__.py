"""CLI wiring: argument parser, dispatch, and helpers shared by commands.

Command groups mirror the Airflow CLI (dags / tasks / variables / connections
/ pools / providers / plugins / config / jobs / assets / backfill / version /
health), backed by REST API v1 or v2 instead of local Airflow internals, plus
``dags deploy`` for shipping DAG files to S3 or a mounted dags folder.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

from ..client import AirflowClient
from ..config import COMMAND_GROUPS, Settings
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

    # ------------------------------------------------------ scope guardrails
    #
    # `dag_id_prefix` keeps a profile pointed at one tenant's DAGs. Handlers
    # validate *before* issuing any request, so an out-of-scope dag_id never
    # reaches the server. This is an agent guardrail, not a security boundary —
    # real isolation has to come from the Airflow server (RBAC / multi-team).

    def _prefix_hint(self) -> str:
        return ", ".join(repr(prefix) for prefix in self.settings.dag_id_prefix)

    def allows_dag_id(self, dag_id: str) -> bool:
        prefixes = self.settings.dag_id_prefix
        return not prefixes or str(dag_id).startswith(prefixes)

    def check_dag_id(self, dag_id: str) -> str:
        """Return dag_id, or fail when the profile's prefix does not cover it."""
        if not self.allows_dag_id(dag_id):
            profile = self.settings.profile_name or "<default>"
            raise UsageError(
                f"dag_id {dag_id!r} is out of scope for profile {profile}: "
                f"dag_id_prefix limits this environment to {self._prefix_hint()}"
            )
        return dag_id

    def check_dag_ids(self, dag_ids: Iterable[str]) -> None:
        """Validate every dag_id up front, so a bad one aborts the whole command."""
        for dag_id in dag_ids:
            self.check_dag_id(dag_id)

    def filter_dag_rows(
        self, rows: List[Dict[str, Any]], key: str = "dag_id"
    ) -> List[Dict[str, Any]]:
        if not self.settings.scoped:
            return rows
        kept = [row for row in rows if self.allows_dag_id(str(row.get(key) or ""))]
        hidden = len(rows) - len(kept)
        if hidden:
            # stderr keeps `-o json` stdout machine-parseable
            print(
                f"note: {hidden} row(s) outside dag_id_prefix {self._prefix_hint()} hidden",
                file=sys.stderr,
            )
        return kept

    def reject_when_scoped(self, command: str, alternative: str) -> None:
        """Refuse commands whose arguments carry no dag_id to check the prefix against."""
        if self.settings.scoped:
            raise UsageError(
                f"`{command}` is unavailable while dag_id_prefix is set: it takes no "
                f"dag_id, so the prefix cannot be enforced — use {alternative} instead"
            )


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


def build_parser(allowed: Optional[Set[str]] = None) -> argparse.ArgumentParser:
    """Build the CLI parser; `allowed` (a profile's allow_commands) drops groups.

    With ``allowed=None`` every group is registered — the manifest's `commands`
    catalogue and permission patterns are validated against that full parser.
    """
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

    registrars = {
        "dags": dags_cmd.register,
        "tasks": tasks_cmd.register,
        "variables": variables_cmd.register,
        "connections": connections_cmd.register,
        "pools": pools_cmd.register,
        "assets": assets_cmd.register,
        "backfill": backfill_cmd.register,
        "version": misc_cmd.register_version,
        "health": misc_cmd.register_health,
        "providers": misc_cmd.register_providers,
        "plugins": misc_cmd.register_plugins,
        "config": misc_cmd.register_config,
        "jobs": misc_cmd.register_jobs,
    }

    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Remote Apache Airflow CLI over REST API v1/v2 (Airflow 2.x/3.x). "
            "Command groups mirror the Airflow CLI; `dags deploy` additionally "
            "ships DAG files to S3 or a local dags folder."
        ),
        epilog="Examples: `datus airflow dags list`, `datus airflow dags trigger my_dag --wait`, "
        "`datus airflow dags deploy ./dags --dest s3://bucket/dags/`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    # COMMAND_GROUPS drives both the order and the completeness of registration
    for name in COMMAND_GROUPS:
        if allowed is None or name in allowed:
            registrars[name](sub)

    return parser


def _reject_disabled_group(
    argv: List[str], allowed: Optional[Set[str]], profile_name: str
) -> None:
    """Turn `allow_commands` misses into a clear policy error.

    Without this the filtered parser would only say "invalid choice", which
    reads like a typo rather than a profile restriction.
    """
    if not allowed:
        return
    group = next((token for token in argv if not token.startswith("-")), None)
    if group is None or group not in COMMAND_GROUPS or group in allowed:
        return  # unknown names stay argparse's job
    profile = profile_name or "<default>"
    raise UsageError(
        f"command group {group!r} is disabled by allow_commands in profile {profile} "
        f"(available: {', '.join(sorted(allowed))})"
    )


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    try:
        settings = Settings.from_profile(profile)
        allowed = settings.allowed_groups()
        _reject_disabled_group(argv, allowed, settings.profile_name)
    except PluginError as exc:  # bad profile / disabled group: report before parsing
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    parser = build_parser(allowed)
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # -h or usage error; keep the CLI convention
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else EXIT_USAGE

    try:
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
