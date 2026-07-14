"""The Datus plugin entry point (registered as `mwaa` under `datus.plugins`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import summarize_aws_profile


class MwaaPlugin:
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
        """Environment inspection and token minting run everywhere; `cli run` is
        an opaque Airflow-CLI passthrough (the wrapped command could be
        destructive), so it always requires confirmation in both profiles."""
        read_only = [
            "environments list:*", "environments get:*",
            "token web-login:*", "token cli:*",
        ]
        always_ask = ["cli run:*"]
        return {
            "normal": {"allow": read_only, "ask": always_ask},
            "auto": {"allow": read_only, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## MWAA (installed, not configured)\n"
                "The `datus mwaa` CLI is installed but has no environment configured.\n"
                "Run the `mwaa-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg, extra_fields=('environment',))}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## MWAA\n"
            "Inspect Amazon MWAA (Managed Workflows for Apache Airflow) environments and reach "
            "their Airflow through `datus mwaa <group> <subcommand>` (`--profile <env>` before "
            "the group).\n"
            "Groups: `environments` (list, get), `token` (web-login, cli — mint short-lived "
            "tokens), `cli run` (run an Airflow CLI command over REST). For fine-grained, "
            "permission-classified DAG operations prefer the dedicated `datus airflow` plugin "
            "pointed at this environment; `cli run` is an opaque passthrough and always "
            "confirmed. Environment create/update/delete is out of scope. Add `-o json` for "
            "machine-readable output.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
