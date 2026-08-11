"""Turn the profile dict handed over by Datus into validated settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from datus_aws_common import AwsSettings, ConfigError, validate_keys


@dataclass
class Settings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    profile_name: str = ""
    bucket: Optional[str] = None  # default bucket for bare-key arguments
    kms_key_id: Optional[str] = None  # SSE-KMS key for writes
    compatibility: str = "aws"

    @classmethod
    def from_profile(cls, profile) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, {"bucket", "kms_key_id", "compatibility"}, "plugins.s3.<profile>")
        compatibility = str(data.get("compatibility") or "aws").lower()
        if compatibility not in {"aws", "minio", "aliyun-oss"}:
            raise ConfigError("compatibility must be aws, minio, or aliyun-oss")
        if compatibility == "aliyun-oss" and data.get("kms_key_id"):
            raise ConfigError("kms_key_id uses AWS SSE-KMS semantics and is unavailable for aliyun-oss")
        settings = cls(
            aws=AwsSettings.from_profile(data),
            profile_name=str(data.get("name", "") or ""),
            compatibility=compatibility,
        )
        if data.get("bucket"):
            settings.bucket = str(data["bucket"])
        if data.get("kms_key_id"):
            settings.kms_key_id = str(data["kms_key_id"])
        return settings
