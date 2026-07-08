"""CLI wiring for `datus cloudwatch`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus cloudwatch"


def build_parser() -> argparse.ArgumentParser:
    from . import alarms_cmd, dashboards_cmd, logs_cmd, metrics_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Query AWS CloudWatch logs, metrics, alarms and dashboards.",
        epilog="Examples: `datus cloudwatch logs tail /aws/lambda/fn --follow`, "
        "`datus cloudwatch logs insights /aws/glue/jobs -q 'fields @message' --start -1h`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    logs_cmd.register(sub)
    metrics_cmd.register(sub)
    alarms_cmd.register(sub)
    dashboards_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
