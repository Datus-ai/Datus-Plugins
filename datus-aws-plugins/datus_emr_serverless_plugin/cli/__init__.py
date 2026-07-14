"""CLI wiring for `datus emr-serverless`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus emr-serverless"


def build_parser() -> argparse.ArgumentParser:
    from . import applications_cmd, jobs_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Operate AWS EMR Serverless applications and Spark job runs.",
        epilog="Examples: `datus emr-serverless applications start app-123`, "
        "`datus emr-serverless jobs run app-123 --entry-point s3://bkt/job.py --wait`, "
        "`datus emr-serverless jobs dashboard app-123 jr-456`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    applications_cmd.register(sub)
    jobs_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
