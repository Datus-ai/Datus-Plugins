"""CLI wiring for `datus s3`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus s3"


def build_parser() -> argparse.ArgumentParser:
    from . import buckets_cmd, objects_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Browse and move S3 data (ls/stat/cat/cp/sync/rm/presign + S3 Select).",
        epilog="Examples: `datus s3 ls s3://bucket/prefix/`, "
        "`datus s3 select s3://b/data.csv --format csv --sql \"select * from s3object limit 5\"`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    objects_cmd.register(sub)
    buckets_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
