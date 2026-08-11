from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datus_gcp_common import GcpSettings, validate_keys


@dataclass(frozen=True)
class Settings:
    gcp: GcpSettings
    bucket: str | None = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, {"bucket"}, "plugins.gcs.<profile>")
        return cls(
            GcpSettings.from_profile(data),
            str(data.get("bucket") or "").strip() or None,
        )
