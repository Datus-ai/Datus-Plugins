"""CLI wiring for `datus mwaa`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus mwaa"


def build_parser() -> argparse.ArgumentParser:
    from . import cli_cmd, environments_cmd, token_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Inspect Amazon MWAA environments, mint tokens, and run the Airflow CLI over REST.",
        epilog="Examples: `datus mwaa environments list`, "
        "`datus mwaa cli run 'dags list' --env prod`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    environments_cmd.register(sub)
    token_cmd.register(sub)
    cli_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
