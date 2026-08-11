"""Turn the resolved EKS profile dict into validated settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from datus_aws_common import AwsSettings, ConfigError, validate_keys

EXTRA_KEYS = {"cluster"}


@dataclass
class Settings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    cluster: str = ""
    profile_name: str = ""

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, EXTRA_KEYS, "plugins.eks.<profile>")
        cluster = str(data.get("cluster") or "").strip()
        if not cluster:
            raise ConfigError(
                "cluster is required under agent.plugins.eks.<profile>; "
                "run the eks-setup skill for guided configuration"
            )
        return cls(
            aws=AwsSettings.from_profile(data),
            cluster=cluster,
            profile_name=str(data.get("name") or "").strip(),
        )
