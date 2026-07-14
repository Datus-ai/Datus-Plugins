"""The Datus plugin entry point (registered as `cloudwatch` under `datus.plugins`).

Datus constructs this class per invocation with the resolved profile dict and
calls `run_cli`; `skills_dir` / `system_prompt` / `cli_permissions` are resolved
at startup and must stay class-reachable. This package never imports datus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import summarize_aws_profile


class CloudWatchPlugin:
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
        """Bash-permission rules for `datus cloudwatch ...` when run by the agent.

        Everything CloudWatch exposes here is read-only except `alarms set-state`
        (a reversible operate action used for testing/suppression), which is
        routine: confirmed under `normal`, auto-run under `auto`.
        """
        read_only = [
            "logs groups:*", "logs streams:*", "logs get:*", "logs tail:*", "logs insights:*",
            "metrics list:*", "metrics get:*",
            "alarms list:*", "alarms get:*", "alarms history:*",
            "dashboards list:*", "dashboards get:*",
        ]
        routine = ["alarms set-state:*"]
        return {
            "normal": {"allow": read_only, "ask": routine},
            "auto": {"allow": read_only + routine, "ask": []},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## CloudWatch (installed, not configured)\n"
                "The `datus cloudwatch` CLI is installed but has no environment configured.\n"
                "Run the `cloudwatch-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg)}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## CloudWatch\n"
            "Query AWS CloudWatch through `datus cloudwatch <group> <subcommand>` "
            "(`--profile <env>` before the group).\n"
            "Groups: `logs` (groups, streams, get, tail [--follow], insights), `metrics` "
            "(list, get), `alarms` (list, get, history, set-state), `dashboards` (list, get). "
            "Add `-o json` for machine-readable output. Consult the `cloudwatch` skill before "
            "composing non-trivial commands (esp. Logs Insights queries).\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
