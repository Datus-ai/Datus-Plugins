"""The Datus plugin entry point (registered as `glue` under `datus.plugins`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import summarize_aws_profile


class GluePlugin:
    def __init__(self, profile: Optional[Dict[str, Any]] = None) -> None:
        self.profile: Dict[str, Any] = dict(profile or {})

    def run_cli(self, argv: List[str]) -> int:
        from .cli import main

        return main(list(argv), self.profile)

    @classmethod
    def skills_dir(cls) -> str:
        return str(Path(__file__).parent / "skills")

    @classmethod
    def cli_permissions(cls) -> Dict[str, Dict[str, List[str]]]:
        """Catalog reads + crawler/job inspection run everywhere; crawler runs and
        job stops are routine (crawlers only touch metadata); running a job (writes
        data + billed) and any catalog mutation always require confirmation."""
        read_only = [
            "catalog databases:*", "catalog tables:*", "catalog show:*", "catalog search:*",
            "catalog partitions:*", "catalog versions:*", "catalog stats:*",
            "crawlers list:*", "crawlers get:*", "crawlers status:*", "crawlers history:*",
            "crawlers metrics:*",
            "jobs list:*", "jobs get:*", "jobs runs:*", "jobs run-status:*", "jobs logs:*",
            "connections list:*", "connections get:*",
        ]
        routine = [
            "crawlers run:*", "crawlers stop:*", "crawlers schedule-pause:*", "crawlers schedule-resume:*",
            "jobs stop:*", "jobs bookmark-reset:*",
        ]
        always_ask = [
            "jobs run:*",
            "catalog create-database:*", "catalog delete-database:*",
            "catalog create-table:*", "catalog update-table:*", "catalog delete-table:*",
            "catalog add-partition:*", "catalog delete-partition:*",
        ]
        return {
            "normal": {"allow": read_only, "ask": routine + always_ask},
            "auto": {"allow": read_only + routine, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## Glue (installed, not configured)\n"
                "The `datus glue` CLI is installed but has no environment configured.\n"
                "Run the `glue-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg, extra_fields=('catalog_id',))}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## Glue\n"
            "Operate the AWS Glue Data Catalog, crawlers and ETL jobs through "
            "`datus glue <group> <subcommand>` (`--profile <env>` before the group).\n"
            "Groups: `catalog` (databases, tables, show, search, partitions, versions, stats, "
            "and create/update/delete of databases/tables/partitions), `crawlers` (list, get, "
            "status, run [--wait], stop, history, metrics, schedule-pause/resume), `jobs` (list, "
            "get, run [--wait], run-status, runs, stop, logs, bookmark-reset), `connections` "
            "(list, get). `catalog show` renders a table's schema; `jobs logs` reads the run's "
            "CloudWatch logs. Add `-o json` for machine-readable output. Consult the `glue` skill "
            "before composing non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
