"""Turn the profile dict handed over by Datus into validated settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from datus_aws_common import AwsSettings, validate_keys


@dataclass
class Settings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    profile_name: str = ""
    cluster: Optional[str] = None  # default cluster name/arn
    log_group: Optional[str] = None  # CloudWatch log group for `tasks logs`

    @classmethod
    def from_profile(cls, profile) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, {"cluster", "log_group"}, "plugins.ecs.<profile>")
        settings = cls(
            aws=AwsSettings.from_profile(data),
            profile_name=str(data.get("name", "") or ""),
        )
        if data.get("cluster"):
            settings.cluster = str(data["cluster"])
        if data.get("log_group"):
            settings.log_group = str(data["log_group"])
        return settings
