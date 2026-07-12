"""The Datus plugin entry point (registered as `emr-serverless` under `datus.plugins`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import aws_config_schema, summarize_aws_profile, validate_aws_profile


class EmrServerlessPlugin:
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
        """Profile fields for the `/plugins` TUI form (shared AWS keys + EMR Serverless extras)."""
        return aws_config_schema(extra_fields=[
            {"name": "application_id", "description": "Default EMR Serverless application ID"},
            {"name": "execution_role_arn", "description": "Default IAM role ARN for job runs"},
        ])

    @classmethod
    def validate_profile(cls, profile: Dict[str, Any]) -> List[str]:
        """Shape-check a candidate profile before it is saved (${VAR} left opaque)."""
        return validate_aws_profile(profile, extra_keys=("application_id", "execution_role_arn"))

    @classmethod
    def cli_permissions(cls) -> Dict[str, Dict[str, List[str]]]:
        """Application/job reads and the live Spark dashboard run everywhere;
        starting/stopping an application and cancelling a job run are routine;
        running a job (writes data + billed) always requires confirmation."""
        read_only = [
            "applications list:*", "applications get:*",
            "jobs list:*", "jobs run-status:*", "jobs dashboard:*",
        ]
        routine = [
            "applications start:*", "applications stop:*", "jobs cancel:*",
        ]
        always_ask = [
            "jobs run:*",
        ]
        return {
            "normal": {"allow": read_only, "ask": routine + always_ask},
            "auto": {"allow": read_only + routine, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## EMR Serverless (installed, not configured)\n"
                "The `datus emr-serverless` CLI is installed but has no environment configured.\n"
                "Run the `emr-serverless-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg, extra_fields=('application_id',))}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## EMR Serverless\n"
            "Operate AWS EMR Serverless applications and Spark job runs through "
            "`datus emr-serverless <group> <subcommand>` (`--profile <env>` before the group).\n"
            "Groups: `applications` (list, get, start, stop) and `jobs` (run [--wait], list, "
            "run-status, cancel, dashboard). An application must be STARTED before it can run "
            "jobs; `jobs run` submits a Spark job (writes data, billed) and with `--wait` polls "
            "until the run is terminal; `jobs dashboard` returns the live Spark UI URL. Add "
            "`-o json` for machine-readable output. Consult the `emr-serverless` skill before "
            "composing non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
