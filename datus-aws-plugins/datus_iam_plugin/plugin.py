"""The Datus plugin entry point (registered as `iam` under `datus.plugins`).

Datus constructs this class per invocation with the resolved profile dict and
calls `run_cli`; `skills_dir` / `system_prompt` / `cli_permissions` are resolved
at startup and must stay class-reachable. This package never imports datus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import summarize_aws_profile


class IamPlugin:
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
        """Bash-permission rules for `datus iam ...` when run by the agent.

        Everything IAM exposes here is pure read-only diagnostics (Get*/List*,
        SimulatePrincipalPolicy, GetCallerIdentity) — there are no mutating
        commands — so every command is allowed under both `normal` and `auto`.
        """
        read_only = [
            "whoami:*",
            "roles list:*", "roles get:*", "roles attached:*", "roles trust:*",
            "users list:*", "users get:*", "users attached:*",
            "policies list:*", "policies get:*", "policies document:*",
            "simulate principal:*", "simulate custom:*",
        ]
        return {
            "normal": {"allow": read_only, "ask": []},
            "auto": {"allow": read_only, "ask": []},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## IAM (installed, not configured)\n"
                "The `datus iam` CLI is installed but has no environment configured.\n"
                "Run the `iam-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg)}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## IAM\n"
            "Inspect AWS IAM (read-only) through `datus iam <command>` "
            "(`--profile <env>` before the command).\n"
            "Commands: `whoami` (STS caller identity), `roles` (list, get, attached, trust), "
            "`users` (list, get, attached), `policies` (list, get, document), `simulate` "
            "(principal, custom) — the key diagnostic for `AccessDenied`. Everything is "
            "read-only. Add `-o json` for machine-readable output. Consult the `iam` skill "
            "before composing non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
