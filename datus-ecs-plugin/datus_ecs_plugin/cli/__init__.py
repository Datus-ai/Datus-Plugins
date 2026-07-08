"""CLI wiring for `datus ecs`: parser assembly and dispatch."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from datus_aws_common import AwsContext, run

from ..config import Settings

PROG = "datus ecs"


def build_parser() -> argparse.ArgumentParser:
    from . import clusters_cmd, services_cmd, taskdefs_cmd, tasks_cmd

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Operate existing Amazon ECS / Fargate clusters, services and tasks.",
        epilog="Examples: `datus ecs services scale prod web 4`, "
        "`datus ecs tasks run --task-def etl:12 --launch-type FARGATE --wait`",
    )
    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    clusters_cmd.register(sub)
    services_cmd.register(sub)
    tasks_cmd.register(sub)
    taskdefs_cmd.register(sub)

    return parser


def main(argv: List[str], profile: Dict[str, Any]) -> int:
    parser = build_parser()
    return run(parser, argv, lambda: AwsContext(Settings.from_profile(profile)))
