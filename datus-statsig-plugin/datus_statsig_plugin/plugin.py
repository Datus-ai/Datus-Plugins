"""The Datus plugin entry point (registered as `statsig` under `datus.plugins`).

Datus constructs this class per invocation with the resolved profile dict and
calls `run_cli`; `skills_dir` / `system_prompt` / `cli_permissions` are resolved
at startup and so must stay class-reachable. This package never imports datus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DEFAULT_API_VERSION, DEFAULT_BASE_URL, DEFAULT_TIMEOUT


class StatsigPlugin:
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
        """Bash-permission rules for `datus statsig ...` when run by the agent.

        Patterns are namespace-relative (Datus prefixes `datus statsig `).
        Policy: read-only commands (and `describe`) auto-run everywhere; every
        state-changing command — metric/source authoring, WHN recompute, ETL
        backfill/schedule, warehouse-connection updates — confirms under both
        profiles (re-running them costs warehouse compute and hits Statsig's
        mutation rate limit, so `auto` does not promote them).
        """
        read_only = [
            "metrics list:*", "metrics get:*", "metrics sql:*", "metrics values:*",
            "metric-source list:*", "metric-source get:*",
            "experiments list:*", "experiments get:*", "experiments pulse:*",
            "experiments summary:*", "experiments exposures:*", "experiments pulse-status:*",
            "ingestion get:*", "ingestion runs:*", "ingestion run:*", "ingestion status:*",
            "ingestion schedule-get:*",
            "events list:*", "events get:*",
            "logs query:*",
            "reports get:*",
            "describe:*",
        ]
        always_ask = [
            "metrics create:*", "metrics update:*", "metrics reload:*",
            "metric-source create:*", "metric-source update:*", "metric-source delete:*",
            "experiments load-pulse:*",
            "ingestion backfill:*", "ingestion schedule-set:*",
            "warehouse-connections update:*",
        ]
        return {
            "normal": {"allow": read_only, "ask": always_ask},
            "auto": {"allow": read_only, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## Statsig (installed, not configured)\n"
                "The `datus statsig` CLI is installed but has no environment configured.\n"
                "Run the `statsig-setup` skill to configure one."
            )

        # Allow-list only non-secret fields; profiles also carry the api_key.
        lines = []
        for name, cfg in profiles.items():
            cfg = cfg or {}
            api = cfg.get("api_base_url") or cfg.get("base_url") or "https://statsigapi.net"
            version = cfg.get("api_version") or "20240601"
            lines.append(f"- {name}: api={api}, version={version}")
        environments = "\n".join(lines)

        return (
            "## Statsig\n"
            "Query the Statsig Console API through `datus statsig <group> <subcommand>` "
            "(`--profile <env>` before the group).\n"
            "Groups: `metrics` (list/get/sql/values + create/update/reload), `metric-source` "
            "(warehouse-native SQL sources: list/get + create/update/delete), `experiments` "
            "(list/get + pulse/summary/exposures readouts, load-pulse/pulse-status), `ingestion` "
            "(ETL: get/runs/run/status/schedule-get + backfill/schedule-set), "
            "`warehouse-connections update`, `events`, `logs query`, `reports get`. "
            "`describe <group> <subcommand>` prints the JSON body template for a write command "
            "(also on `--help`). Reads run freely; every mutation confirms. Add `-o json` "
            "(default) or `--compact` for machine output. Consult the `statsig` skill before "
            "composing non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )

    @classmethod
    def config_schema(cls) -> List[Dict[str, Any]]:
        """Describe the profile fields for the Datus `/plugins` TUI form.

        `api_key` is the only required field and is a secret (Datus renders a
        `${ENV_VAR}` hint and never echoes it). The rest carry sensible
        defaults so a minimal profile is just an api_key.
        """
        return [
            {
                "name": "api_key",
                "description": "Statsig Console API key (create at console.statsig.com/api_keys)",
                "required": True,
                "secret": True,
            },
            {
                "name": "api_base_url",
                "description": "Statsig API base URL",
                "required": False,
                "default": DEFAULT_BASE_URL,
            },
            {
                "name": "api_version",
                "description": "Statsig Console API version (YYYYMMDD)",
                "required": False,
                "default": DEFAULT_API_VERSION,
            },
            {
                "name": "timeout",
                "description": "HTTP request timeout in seconds",
                "required": False,
                "default": DEFAULT_TIMEOUT,
            },
        ]

    @classmethod
    def validate_profile(cls, profile: Dict[str, Any]) -> List[str]:
        """Shape-check a candidate profile before Datus persists it.

        Receives raw (unexpanded) values, so `${ENV_VAR}` placeholders are
        treated as opaque. `Settings.from_profile` remains the runtime guard.
        """
        errors: List[str] = []
        data = dict(profile or {})
        if not (data.get("api_key") or data.get("token")):
            errors.append("api_key is required (use a ${ENV_VAR} placeholder for the secret value).")
        raw_url = data.get("api_base_url") or data.get("base_url")
        if raw_url and not str(raw_url).startswith(("http://", "https://", "${")):
            errors.append("api_base_url must start with http:// or https://")
        timeout = data.get("timeout")
        if timeout is not None and str(timeout).strip() and not str(timeout).startswith("${"):
            try:
                float(timeout)
            except (TypeError, ValueError):
                errors.append(f"timeout must be a number (got {timeout!r})")
        return errors
