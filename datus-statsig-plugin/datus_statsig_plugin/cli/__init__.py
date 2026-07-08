"""CLI wiring: argument parser, dispatch, and helpers shared by commands.

Command groups map onto the Statsig Console API resources most relevant to data
analysis: ``metrics`` / ``metric-source`` (read + warehouse-native SQL
authoring), ``experiments`` (pulse/summary/exposure readouts + WHN pulse
compute), ``ingestion`` (ETL runs/backfills), ``warehouse-connections``,
``events``, ``logs``, ``reports``, plus a read-only ``describe`` that prints the
request-body template for a write command.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

import requests

from ..config import Settings
from ..errors import EXIT_USAGE, PluginError, UsageError
from ..output import DEFAULT_FORMAT, FORMATS
from ..schemas import ONE_OF_FIELDS, REQUIRED_FIELDS

PROG = "datus statsig"


class Context:
    """Carries settings and a lazily-created API client into command handlers."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from ..client import StatsigClient

            self._client = StatsigClient(self.settings)
        return self._client


# ------------------------------------------------------------------ helpers


def add_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        choices=FORMATS,
        default=DEFAULT_FORMAT,
        help="output format (default: json; table/plain show curated columns)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="single-line JSON (overrides -o; for piping / token thrift)",
    )


def resolve_fmt(ns: argparse.Namespace) -> str:
    return "compact" if getattr(ns, "compact", False) else getattr(ns, "output", DEFAULT_FORMAT)


def parse_json_arg(raw: str, what: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise UsageError(f"{what} is not valid JSON: {exc}") from exc


def load_body(ns: argparse.Namespace, key: str) -> Dict[str, Any]:
    """Read a request body from --json / --json-file and validate it for `key`.

    `key` is the "<group> <subcommand>" path (matches schemas + permissions).
    Raises UsageError (exit 2) with a field-level message the model can correct.
    """
    inline = getattr(ns, "json", None)
    path = getattr(ns, "json_file", None)
    if inline and path:
        raise UsageError("pass either --json or --json-file, not both")
    if inline:
        body = parse_json_arg(inline, "--json")
    elif path:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            raise UsageError(f"cannot read --json-file {path!r}: {exc}") from exc
        body = parse_json_arg(raw, f"--json-file {path}")
    else:
        raise UsageError(
            f"{key} needs a request body: pass --json '<json>' or --json-file <path> "
            f"(run `datus statsig describe {key}` for the field template)"
        )
    if not isinstance(body, dict):
        raise UsageError("request body must be a JSON object")

    missing = [
        f for f in REQUIRED_FIELDS.get(key, []) if not body.get(f) and body.get(f) not in (0, False)
    ]
    if missing:
        raise UsageError(
            f"{key}: missing required field(s): {', '.join(missing)} "
            f"(run `datus statsig describe {key}`)"
        )
    one_of = ONE_OF_FIELDS.get(key)
    if one_of and not any(body.get(f) for f in one_of):
        raise UsageError(f"{key}: body must contain one of: {', '.join(one_of)}")

    if getattr(ns, "dry_run", False):
        body["dryRun"] = True
    return body


def quote_path_part(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    from . import (
        describe_cmd,
        events_cmd,
        experiments_cmd,
        ingestion_cmd,
        logs_cmd,
        metric_source_cmd,
        metrics_cmd,
        reports_cmd,
        warehouse_cmd,
    )

    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Statsig Console API CLI (version 20240601): read metrics & experiment "
            "results, author warehouse-native metric SQL, and drive ingestion/ETL. "
            "`--profile <env>` selects the configured environment."
        ),
        epilog="Examples: `datus statsig metrics list`, "
        "`datus statsig experiments pulse <id> --control ctrl --test test`, "
        "`datus statsig describe metric-source create`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    metrics_cmd.register(sub)
    metric_source_cmd.register(sub)
    experiments_cmd.register(sub)
    ingestion_cmd.register(sub)
    warehouse_cmd.register(sub)
    events_cmd.register(sub)
    logs_cmd.register(sub)
    reports_cmd.register(sub)
    describe_cmd.register(sub)

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

    settings: Optional[Settings] = None
    try:
        settings = Settings.from_profile(profile)
        ctx = Context(settings)
        rc = ns.func(ctx, ns)
        return 0 if rc is None else int(rc)
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except requests.exceptions.SSLError as exc:
        print(f"error: TLS verification failed: {exc}", file=sys.stderr)
        return 1
    except requests.exceptions.ConnectionError as exc:
        print(f"error: cannot reach the Statsig API: {exc}", file=sys.stderr)
        return 1
    except requests.exceptions.Timeout:
        timeout = settings.timeout if settings else "?"
        print(
            f"error: request timed out after {timeout}s "
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
