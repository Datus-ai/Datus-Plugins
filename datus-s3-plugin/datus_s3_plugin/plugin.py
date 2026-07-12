"""The Datus plugin entry point (registered as `s3` under `datus.plugins`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from datus_aws_common import aws_config_schema, summarize_aws_profile, validate_aws_profile


class S3Plugin:
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
        """Profile fields for the `/plugins` TUI form (shared AWS keys + S3 extras)."""
        return aws_config_schema(extra_fields=[
            {"name": "bucket", "description": "Default bucket for bare-key arguments"},
            {"name": "kms_key_id", "description": "SSE-KMS key ID for object writes"},
        ])

    @classmethod
    def validate_profile(cls, profile: Dict[str, Any]) -> List[str]:
        """Shape-check a candidate profile before it is saved (${VAR} left opaque)."""
        return validate_aws_profile(profile, extra_keys=("bucket", "kms_key_id"))

    @classmethod
    def cli_permissions(cls) -> Dict[str, Dict[str, List[str]]]:
        """Read/list/preview/select/presign run everywhere; object writes
        (cp/sync/mv) are routine; deletes (rm) always require confirmation."""
        read_only = [
            "ls:*", "stat:*", "cat:*", "head:*", "presign:*", "select:*",
            "buckets list:*", "buckets location:*",
        ]
        routine = ["cp:*", "sync:*", "mv:*"]
        always_ask = ["rm:*"]
        return {
            "normal": {"allow": read_only, "ask": routine + always_ask},
            "auto": {"allow": read_only + routine, "ask": always_ask},
        }

    @classmethod
    def system_prompt(cls, profiles: Dict[str, Dict[str, Any]]) -> str:
        if not profiles:
            return (
                "## S3 (installed, not configured)\n"
                "The `datus s3` CLI is installed but has no environment configured.\n"
                "Run the `s3-setup` skill to configure one."
            )
        lines = [f"- {name}: {summarize_aws_profile(cfg, extra_fields=('bucket',))}" for name, cfg in profiles.items()]
        environments = "\n".join(lines)
        return (
            "## S3\n"
            "Browse and move S3 data through `datus s3 <command>` (`--profile <env>` first).\n"
            "Commands: `ls`, `stat`, `cat`, `head`, `presign`, `select` (S3 Select SQL over "
            "CSV/JSON/Parquet), `cp`/`sync`/`mv` (object writes), `rm` (delete, prompts unless "
            "`-y`), `buckets list|location`. URIs are `s3://bucket/key`. Add `-o json` for "
            "machine-readable output. Consult the `s3` skill before non-trivial commands.\n"
            f"Environments ({len(profiles)}):\n{environments}"
        )
