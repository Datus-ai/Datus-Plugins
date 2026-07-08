"""Turn the profile dict handed over by Datus into validated settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from datus_aws_common import AwsSettings, validate_keys


@dataclass
class Settings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    profile_name: str = ""
    application_id: Optional[str] = None  # default EMR Serverless application
    execution_role_arn: Optional[str] = None  # default IAM role for job runs

    @classmethod
    def from_profile(cls, profile) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, {"application_id", "execution_role_arn"}, "plugins.emr-serverless.<profile>")
        settings = cls(
            aws=AwsSettings.from_profile(data),
            profile_name=str(data.get("name", "") or ""),
        )
        if data.get("application_id"):
            settings.application_id = str(data["application_id"])
        if data.get("execution_role_arn"):
            settings.execution_role_arn = str(data["execution_role_arn"])
        return settings
