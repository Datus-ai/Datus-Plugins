"""CLI wiring for `datus emr`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus emr"


def build_parser() -> argparse.ArgumentParser:
    from . import clusters_cmd, steps_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Submit and monitor steps on existing Amazon EMR (on EC2) clusters.",
        epilog="Examples: `datus emr clusters list`, "
        "`datus emr steps add j-XXX --name load --command 'spark-submit s3://b/job.py' --wait`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    clusters_cmd.register(sub)
    steps_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
