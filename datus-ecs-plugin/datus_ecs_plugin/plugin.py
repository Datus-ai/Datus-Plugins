"""The Datus plugin entry point (registered as `ecs` under `datus.plugins`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import aws_config_schema, summarize_aws_profile, validate_aws_profile


class EcsPlugin:
    def __init__(self, profile: Optional[Dict[str, Any]] = None) -> None:
        self.profile: Dict[str, Any] = dict(profile or {})

    def run_cli(self, argv: List[str]) -> int:
        from .cli import main

        return main(list(argv), self.profile)

    @classmethod
    def skills_dir(cls) -> str:
        return str(Path(__file__).parent / "skills")

    @classmethod
    def config_schema(cls) -> List[Dict[str, Any]]:
        """Profile fields for the `/plugins` TUI form (shared AWS keys + ECS extras)."""
        return aws_config_schema(extra_fields=[
            {"name": "cluster", "description": "Default ECS cluster name or ARN"},
            {"name": "log_group", "description": "CloudWatch log group for `tasks logs`"},
        ])

    @classmethod
    def validate_profile(cls, profile: Dict[str, Any]) -> List[str]:
        """Shape-check a candidate profile before it is saved (${VAR} left opaque)."""
        return validate_aws_profile(profile, extra_keys=("cluster", "log_group"))

    @classmethod
    def cli_permissions(cls) -> Dict[str, Dict[str, List[str]]]:
        """Cluster/service/task inspection and logs run everywhere; scaling a
        service and stopping a task are routine; running a task (starts billed
        compute) always confirms. Cluster/service create/delete is out of scope."""
        read_only = [
            "clusters list:*", "clusters describe:*",
            "services list:*", "services describe:*", "services events:*",
            "tasks list:*", "tasks describe:*", "tasks logs:*",
            "task-defs list:*", "task-defs describe:*",
        ]
        routine = ["services scale:*", "tasks stop:*"]
        always_ask = ["tasks run:*"]
        return {
            "normal": {"allow": read_only, "ask": routine + always_ask},
            "auto": {"allow": read_only + routine, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## ECS (installed, not configured)\n"
                "The `datus ecs` CLI is installed but has no environment configured.\n"
                "Run the `ecs-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg, extra_fields=('cluster',))}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## ECS\n"
            "Operate existing Amazon ECS / Fargate clusters through "
            "`datus ecs <group> <subcommand>` (`--profile <env>` before the group).\n"
            "Groups: `clusters` (list, describe), `services` (list, describe, events, scale), "
            "`tasks` (list, describe, run [--wait], stop, logs), `task-defs` (list, describe). "
            "Fargate is `tasks run --launch-type FARGATE`. This drives EXISTING clusters/services "
            "— create/delete and task-def registration are out of scope. `tasks run` starts "
            "billed compute; `tasks logs` needs a configured log_group. Add `-o json` for "
            "machine-readable output. Consult the `ecs` skill before composing non-trivial "
            "commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
