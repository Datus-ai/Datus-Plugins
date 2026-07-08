"""Turn the profile dict handed over by Datus into validated settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from datus_aws_common import AwsSettings, validate_keys


@dataclass
class Settings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    profile_name: str = ""
    cluster_id: Optional[str] = None  # default cluster for `steps` commands
    log_uri: Optional[str] = None  # S3 base for step logs (for `steps logs`)

    @classmethod
    def from_profile(cls, profile) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, {"cluster_id", "log_uri"}, "plugins.emr.<profile>")
        settings = cls(
            aws=AwsSettings.from_profile(data),
            profile_name=str(data.get("name", "") or ""),
        )
        if data.get("cluster_id"):
            settings.cluster_id = str(data["cluster_id"])
        if data.get("log_uri"):
            settings.log_uri = str(data["log_uri"])
        return settings
