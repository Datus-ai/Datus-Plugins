"""Turn the profile dict handed over by Datus into validated settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from datus_aws_common import AwsSettings, validate_keys


@dataclass
class Settings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    profile_name: str = ""
    catalog_id: Optional[str] = None  # cross-account Data Catalog id

    @classmethod
    def from_profile(cls, profile) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, {"catalog_id"}, "plugins.glue.<profile>")
        settings = cls(
            aws=AwsSettings.from_profile(data),
            profile_name=str(data.get("name", "") or ""),
        )
        if data.get("catalog_id"):
            settings.catalog_id = str(data["catalog_id"])
        return settings
