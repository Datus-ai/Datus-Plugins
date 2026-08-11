from __future__ import annotations

from dataclasses import dataclass

from datus_gcp_common import UsageError


@dataclass(frozen=True)
class GcsPath:
    bucket: str
    key: str

    @property
    def uri(self) -> str:
        return f"gs://{self.bucket}/{self.key}"

    def is_prefix(self) -> bool:
        return not self.key or self.key.endswith("/")


def is_gcs(value: str) -> bool:
    return str(value).startswith("gs://")


def parse_gcs_uri(value: str, default_bucket: str | None) -> GcsPath:
    text = str(value or "")
    if text.startswith("gs://"):
        rest = text[5:]
        bucket, _, key = rest.partition("/")
    else:
        bucket, key = str(default_bucket or ""), text.lstrip("/")
    if not bucket:
        raise UsageError("a gs:// bucket or profile bucket is required")
    return GcsPath(bucket, key)
