"""CLI wiring for `datus iam`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus iam"


def build_parser() -> argparse.ArgumentParser:
    from . import policies_cmd, roles_cmd, simulate_cmd, users_cmd, whoami_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Inspect AWS IAM read-only: roles, users, policies, and permission simulation.",
        epilog="Examples: `datus iam whoami`, `datus iam roles attached my-role`, "
        "`datus iam simulate principal arn:aws:iam::1:role/r --action s3:GetObject --resource '*'`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    whoami_cmd.register(sub)
    roles_cmd.register(sub)
    users_cmd.register(sub)
    policies_cmd.register(sub)
    simulate_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
