"""The Datus plugin entry point (registered as `airflow` under `datus.plugins`).

Datus constructs this class per invocation with the resolved profile dict and
calls `run_cli`; `skills_dir` / `system_prompt` are resolved at startup and so
must stay class-reachable. This package never imports datus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


class AirflowPlugin:
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
        """Bash-permission rules for `datus airflow ...` when run by the agent.

        Patterns are namespace-relative (Datus prefixes `datus airflow `).
        Policy: read-only commands auto-run everywhere; routine reversible
        operations need confirmation under `normal` and auto-run under `auto`;
        destructive / code-shipping / run-starting / secret-handling commands
        require confirmation under both profiles.
        """
        read_only = [
            "version:*", "health:*", "plugins:*", "providers:*", "config:*", "jobs:*",
            "dags list:*", "dags details:*", "dags list-runs:*", "dags list-import-errors:*",
            "dags show:*", "dags source:*", "dags state:*", "dags next-execution:*",
            "tasks list:*", "tasks state:*", "tasks states-for-dag-run:*",
            "tasks failed-deps:*", "tasks logs:*",
            "variables list:*", "variables get:*",
            "connections list:*", "connections get:*", "connections test:*",
            "pools list:*", "pools get:*",
            "assets list:*", "assets details:*", "assets events:*",
            "backfill list:*",
        ]
        routine = [
            "dags pause:*", "dags unpause:*", "dags clear-run:*",
            "tasks clear:*",
            "backfill pause:*", "backfill unpause:*", "backfill cancel:*",
            "variables set:*", "pools set:*",
            "variables export:*", "pools export:*",
        ]
        always_ask = [
            # starts new runs (materialize is trigger by another route)
            "dags trigger:*", "assets materialize:*", "backfill create:*",
            # ships executable code to the scheduler; --prune deletes remote files
            "dags deploy:*",
            # deletes files from the dags folder target
            "dags undeploy:*",
            # irreversible or bulk-overwriting
            "dags delete:*", "variables delete:*", "pools delete:*",
            "variables import:*", "connections import:*", "pools import:*",
            # handles connection secrets (add puts them on the command line,
            # export writes them to disk in clear text)
            "connections add:*", "connections delete:*", "connections export:*",
        ]
        return {
            "normal": {"allow": read_only, "ask": routine + always_ask},
            "auto": {"allow": read_only + routine, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## Airflow (installed, not configured)\n"
                "The `datus airflow` CLI is installed but has no environment configured.\n"
                "Run the `airflow-setup` skill to configure one."
            )

        # Allow-list only non-secret fields; profiles contain passwords/tokens.
        lines = []
        for name, cfg in profiles.items():
            cfg = cfg or {}
            if cfg.get("token"):
                auth = "token"
            elif cfg.get("username"):
                auth = "username/password"
            else:
                auth = "none"
            parts = [f"api={cfg.get('api_base_url', '?')}", f"auth={auth}"]
            if cfg.get("dags_folder"):
                parts.append(f"dags_folder={cfg['dags_folder']}")
            lines.append(f"- {name}: " + ", ".join(parts))
        environments = "\n".join(lines)

        return (
            "## Airflow\n"
            "Operate remote Apache Airflow 3.x deployments through `datus airflow <group> "
            "<subcommand>` (REST API backed; supports `--profile <env>` before the group).\n"
            "Groups: `dags` (list, details, list-runs, trigger [--wait], pause/unpause, state, "
            "clear-run, delete, show, source, next-execution, list-import-errors, and `deploy` "
            "to ship DAG files to S3 or a dags folder), `tasks` (list, state, "
            "states-for-dag-run, clear, failed-deps, logs), `variables`, `connections`, "
            "`pools` (each with list/get/set-or-add/delete/import/export), `assets` (list, "
            "details, materialize, events), `backfill` (create/list/pause/unpause/cancel), "
            "`providers list`, `plugins`, `config`, `jobs check`, `version`, `health`.\n"
            "Add `-o json` for machine-readable output. Consult the `airflow` skill for "
            "details before composing non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
