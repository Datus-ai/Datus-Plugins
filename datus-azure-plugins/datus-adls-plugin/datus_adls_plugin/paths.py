from __future__ import annotations

from dataclasses import dataclass

from datus_azure_common import UsageError


@dataclass(frozen=True)
class AdlsPath:
    filesystem: str
    key: str

    @property
    def uri(self) -> str:
        return f"abfss://{self.filesystem}/{self.key}"

    def is_prefix(self) -> bool:
        return not self.key or self.key.endswith("/")


def is_adls(value: str) -> bool:
    return any(
        str(value).startswith(prefix) for prefix in ("abfs://", "abfss://", "adls://")
    )


def parse_adls_uri(value: str, default_filesystem: str | None) -> AdlsPath:
    text = str(value or "")
    for prefix in ("abfss://", "abfs://", "adls://"):
        if text.startswith(prefix):
            rest = text[len(prefix) :]
            filesystem, _, key = rest.partition("/")
            break
    else:
        filesystem, key = str(default_filesystem or ""), text.lstrip("/")
    if not filesystem:
        raise UsageError("an abfss:// filesystem or profile container is required")
    return AdlsPath(filesystem, key)
