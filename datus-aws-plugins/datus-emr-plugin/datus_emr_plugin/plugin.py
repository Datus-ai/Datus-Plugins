"""The Datus plugin entry point (registered as `emr` under `datus.plugins`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import summarize_aws_profile


class EmrPlugin:
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
        """Cluster/step inspection and step logs run everywhere; cancelling a
        step is routine; adding a step (runs billed work) always confirms.
        Cluster provisioning is out of scope (use IaC)."""
        read_only = [
            "clusters list:*", "clusters describe:*", "clusters instances:*",
            "steps list:*", "steps describe:*", "steps logs:*",
        ]
        routine = ["steps cancel:*"]
        always_ask = ["steps add:*"]
        return {
            "normal": {"allow": read_only, "ask": routine + always_ask},
            "auto": {"allow": read_only + routine, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## EMR (installed, not configured)\n"
                "The `datus emr` CLI is installed but has no environment configured.\n"
                "Run the `emr-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg, extra_fields=('cluster_id',))}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## EMR\n"
            "Operate existing Amazon EMR (on EC2) clusters through "
            "`datus emr <group> <subcommand>` (`--profile <env>` before the group).\n"
            "Groups: `clusters` (list, describe, instances) and `steps` (list, describe, "
            "add [--wait], cancel, logs). This drives EXISTING clusters — cluster "
            "provisioning/termination is out of scope (use IaC). `steps add` submits billed "
            "work; `steps logs` reads step stdout/stderr from the configured S3 log_uri. Add "
            "`-o json` for machine-readable output. Consult the `emr` skill before composing "
            "non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
