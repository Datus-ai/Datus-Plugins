"""The Datus plugin entry point (registered as `quicksight` under `datus.plugins`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import aws_config_schema, summarize_aws_profile, validate_aws_profile


class QuickSightPlugin:
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
        """Profile fields for the `/plugins` TUI form (shared AWS keys + QuickSight extras)."""
        return aws_config_schema(extra_fields=[
            {"name": "aws_account_id", "description": "AWS account ID that owns the QuickSight resources (needed by every command)"},
            {"name": "namespace", "description": "QuickSight namespace", "default": "default"},
            {"name": "identity_region", "description": "Region of the QuickSight identity store (users/groups/namespaces)"},
        ])

    @classmethod
    def validate_profile(cls, profile: Dict[str, Any]) -> List[str]:
        """Shape-check a candidate profile before it is saved (${VAR} left opaque)."""
        return validate_aws_profile(
            profile, extra_keys=("aws_account_id", "namespace", "identity_region")
        )

    @classmethod
    def cli_permissions(cls) -> Dict[str, Dict[str, List[str]]]:
        """Reads/list/describe/permissions-get/embed/export run everywhere; SPICE
        refresh run/cancel are routine; every mutation (create/update/delete/
        publish/restore/permission-set/membership/schedule/import) confirms."""
        read_only = [
            "datasets list:*", "datasets describe:*", "datasets permissions:*",
            "datasources list:*", "datasources describe:*",
            "dashboards list:*", "dashboards describe:*", "dashboards versions:*",
            "dashboards permissions:*", "dashboards embed-url:*", "dashboards embed-url-anonymous:*",
            "analyses list:*", "analyses describe:*",
            "templates list:*", "templates describe:*", "templates versions:*",
            "themes list:*", "themes describe:*",
            "folders list:*", "folders describe:*", "folders members:*",
            "users list:*", "users describe:*",
            "groups list:*", "groups describe:*", "groups members:*",
            "namespaces list:*", "namespaces describe:*",
            "refresh list:*", "refresh status:*", "refresh schedules:*",
            "account settings:*", "account subscription:*",
            "assets export:*", "assets export-status:*", "assets import-status:*",
        ]
        routine = ["refresh run:*", "refresh cancel:*"]
        always_ask = [
            "datasets create:*", "datasets update:*", "datasets delete:*", "datasets set-permissions:*",
            "datasources create:*", "datasources update:*", "datasources delete:*",
            "dashboards create:*", "dashboards update:*", "dashboards publish:*",
            "dashboards delete:*", "dashboards set-permissions:*",
            "analyses create:*", "analyses update:*", "analyses delete:*", "analyses restore:*",
            "templates create:*", "templates update:*", "templates delete:*",
            "themes create:*", "themes update:*", "themes delete:*",
            "folders create:*", "folders delete:*", "folders member-add:*", "folders member-remove:*",
            "users register:*", "users update:*", "users delete:*",
            "groups create:*", "groups delete:*", "groups member-add:*", "groups member-remove:*",
            "namespaces create:*", "namespaces delete:*",
            "refresh schedule-put:*", "refresh schedule-delete:*",
            "assets import:*",
        ]
        return {
            "normal": {"allow": read_only, "ask": routine + always_ask},
            "auto": {"allow": read_only + routine, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## QuickSight (installed, not configured)\n"
                "The `datus quicksight` CLI is installed but has no environment configured.\n"
                "Run the `quicksight-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg, extra_fields=('aws_account_id',))}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## QuickSight\n"
            "Manage Amazon QuickSight through `datus quicksight <group> <subcommand>` "
            "(`--profile <env>` before the group).\n"
            "Groups: `datasets`, `datasources`, `dashboards`, `analyses`, `templates`, `themes`, "
            "`folders` (list/describe + create/update/delete), `users`/`groups`/`namespaces` "
            "(identity admin), `refresh` (SPICE ingestions: run [--wait]/status/list/cancel + "
            "schedules), `account` (settings, subscription), `assets` (asset-bundle export/import). "
            "`dashboards embed-url[-anonymous]` mints viewing links. Reads run freely; mutations "
            "(create/update/delete/publish/permissions/membership/import) always confirm; "
            "`refresh run/cancel` are routine. Requires `aws_account_id`; user/group/namespace "
            "ops use `identity_region`. Add `-o json` for machine-readable output. Consult the "
            "`quicksight` skill before non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
