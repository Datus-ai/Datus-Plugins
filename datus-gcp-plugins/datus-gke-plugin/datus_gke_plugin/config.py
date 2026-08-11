from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datus_gcp_common import ConfigError, GcpSettings, validate_keys

EXTRA_KEYS = {"location", "cluster", "endpoint_mode"}


@dataclass(frozen=True)
class Settings:
    gcp: GcpSettings
    location: str
    cluster: str
    endpoint_mode: str = "auto"

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "Settings":
        data = dict(profile or {})
        validate_keys(data, EXTRA_KEYS, "plugins.gke.<profile>")
        location = str(data.get("location") or "").strip()
        cluster = str(data.get("cluster") or "").strip()
        if not location or not cluster:
            raise ConfigError("location and cluster are required in the GKE profile")
        mode = str(data.get("endpoint_mode") or "auto").lower()
        if mode not in {"auto", "public", "private", "dns"}:
            raise ConfigError("endpoint_mode must be auto, public, private, or dns")
        return cls(GcpSettings.from_profile(data), location, cluster, mode)

    @property
    def parent(self) -> str:
        return f"projects/{self.gcp.project}/locations/{self.location}"

    @property
    def cluster_path(self) -> str:
        return f"{self.parent}/clusters/{self.cluster}"
