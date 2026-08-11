from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from datus_azure_common import AzureSettings, ConfigError, validate_keys


@dataclass(frozen=True)
class Settings:
    azure: AzureSettings
    account_url: str
    container: str | None = None
    account_key: str | None = None
    sas_token: str | None = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "Settings":
        data = dict(profile or {})
        validate_keys(
            data,
            {"account_url", "container", "account_key", "sas_token"},
            "plugins.adls.<profile>",
        )
        url = str(data.get("account_url") or "").rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigError("account_url must be an https ADLS dfs endpoint")
        account_key = str(data.get("account_key") or "").strip() or None
        sas = str(data.get("sas_token") or "").strip().lstrip("?") or None
        if account_key and sas:
            raise ConfigError("configure at most one of account_key or sas_token")
        return cls(
            AzureSettings.from_profile(data),
            url,
            str(data.get("container") or "").strip() or None,
            account_key,
            sas,
        )

    @property
    def account_name(self) -> str:
        return urlparse(self.account_url).netloc.split(".")[0]

    @property
    def blob_url(self) -> str:
        return self.account_url.replace(".dfs.", ".blob.")
