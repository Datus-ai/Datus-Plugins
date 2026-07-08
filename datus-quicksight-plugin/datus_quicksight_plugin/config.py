"""Turn the profile dict handed over by Datus into validated settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from datus_aws_common import AwsSettings, ConfigError, validate_keys


@dataclass
class Settings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    profile_name: str = ""
    aws_account_id: Optional[str] = None  # required by every QuickSight API
    namespace: str = "default"
    identity_region: Optional[str] = None  # region of the QuickSight identity store (users/groups/namespaces)

    @classmethod
    def from_profile(cls, profile) -> "Settings":
        data = dict(profile or {})
        validate_keys(
            data,
            {"aws_account_id", "namespace", "identity_region"},
            "plugins.quicksight.<profile>",
        )
        settings = cls(
            aws=AwsSettings.from_profile(data),
            profile_name=str(data.get("name", "") or ""),
        )
        if data.get("aws_account_id"):
            settings.aws_account_id = str(data["aws_account_id"])
        if data.get("namespace"):
            settings.namespace = str(data["namespace"])
        if data.get("identity_region"):
            settings.identity_region = str(data["identity_region"])
        return settings

    def account(self) -> str:
        if not self.aws_account_id:
            raise ConfigError("aws_account_id is required for QuickSight — set it in the profile")
        return self.aws_account_id
