from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datus_azure_common import AzureSettings, ConfigError, validate_keys

EXTRA_KEYS = {
    "subscription_id",
    "resource_group",
    "cluster",
    "kubernetes_server_id",
    "use_private_endpoint",
    "api_version",
}
DEFAULT_SERVER_ID = "6dae42f8-4368-4678-94ff-3960e28e3630"


def _bool(value: Any) -> bool:
    text = str(value or "false").lower()
    if text not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigError("use_private_endpoint must be true or false")
    return text in {"true", "1", "yes"}


@dataclass(frozen=True)
class Settings:
    azure: AzureSettings
    subscription_id: str
    resource_group: str
    cluster: str
    kubernetes_server_id: str = DEFAULT_SERVER_ID
    use_private_endpoint: bool = False
    api_version: str | None = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, EXTRA_KEYS, "plugins.aks.<profile>")
        values = [
            str(data.get(key) or "").strip()
            for key in ("subscription_id", "resource_group", "cluster")
        ]
        if not all(values):
            raise ConfigError(
                "subscription_id, resource_group, and cluster are required in the AKS profile"
            )
        return cls(
            AzureSettings.from_profile(data),
            values[0],
            values[1],
            values[2],
            str(data.get("kubernetes_server_id") or DEFAULT_SERVER_ID).strip(),
            _bool(data.get("use_private_endpoint")),
            str(data.get("api_version") or "").strip() or None,
        )

    @property
    def token_scope(self) -> str:
        value = self.kubernetes_server_id
        return value if value.endswith("/.default") else f"{value}/.default"
