"""S3 URI parsing shared by the command handlers."""

from __future__ import annotations

from typing import NamedTuple, Optional

from datus_aws_common import UsageError


class S3Path(NamedTuple):
    bucket: str
    key: str  # may be "" (bucket root) or end with "/" (a prefix)

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"

    def is_prefix(self) -> bool:
        return self.key == "" or self.key.endswith("/")


def parse_s3_uri(raw: str, default_bucket: Optional[str] = None) -> S3Path:
    """Parse ``s3://bucket/key`` (or a bare key when a default bucket is set)."""
    if raw.startswith("s3://"):
        rest = raw[len("s3://"):]
        bucket, _, key = rest.partition("/")
        if not bucket:
            raise UsageError(f"invalid S3 URI (no bucket): {raw!r}")
        return S3Path(bucket, key)
    if default_bucket:
        return S3Path(default_bucket, raw.lstrip("/"))
    raise UsageError(f"expected an s3://bucket/key URI (got {raw!r}); or set a default `bucket`")


def is_s3(raw: str) -> bool:
    return raw.startswith("s3://")
