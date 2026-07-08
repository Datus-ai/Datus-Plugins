"""CLI wiring for `datus quicksight`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus quicksight"


def build_parser() -> argparse.ArgumentParser:
    from . import (
        account_cmd,
        analyses_cmd,
        assets_cmd,
        dashboards_cmd,
        datasets_cmd,
        datasources_cmd,
        folders_cmd,
        groups_cmd,
        namespaces_cmd,
        refresh_cmd,
        templates_cmd,
        themes_cmd,
        users_cmd,
    )

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Manage Amazon QuickSight assets, identities, SPICE refreshes and asset bundles.",
        epilog="Examples: `datus quicksight datasets list`, "
        "`datus quicksight refresh run <dataset-id> --wait`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    datasets_cmd.register(sub)
    datasources_cmd.register(sub)
    dashboards_cmd.register(sub)
    analyses_cmd.register(sub)
    templates_cmd.register(sub)
    themes_cmd.register(sub)
    folders_cmd.register(sub)
    users_cmd.register(sub)
    groups_cmd.register(sub)
    namespaces_cmd.register(sub)
    refresh_cmd.register(sub)
    account_cmd.register(sub)
    assets_cmd.register(sub)

    return parser


def _make_context(profile: Dict[str, Any]) -> AwsContext:
    settings = Settings.from_profile(profile)
    # Every QuickSight API needs the account id; fail fast (exit 3) before any
    # client is built, rather than surfacing an AWS error mid-command.
    settings.account()
    return AwsContext(settings)


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: _make_context(profile))
