"""CLI wiring for `datus glue`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus glue"


def build_parser() -> argparse.ArgumentParser:
    from . import catalog_cmd, connections_cmd, crawlers_cmd, jobs_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Operate the AWS Glue Data Catalog, crawlers and ETL jobs.",
        epilog="Examples: `datus glue catalog show sales orders`, "
        "`datus glue jobs run daily_etl --wait`, `datus glue crawlers run raw --wait`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    catalog_cmd.register(sub)
    crawlers_cmd.register(sub)
    jobs_cmd.register(sub)
    connections_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
