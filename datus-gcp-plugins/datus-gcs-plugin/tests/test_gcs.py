import argparse
from pathlib import Path

import pytest
import yaml
from datus_gcp_common import UsageError

from datus_gcs_plugin.cli import cmd_mv
from datus_gcs_plugin.paths import parse_gcs_uri


def test_paths_support_default_bucket():
    assert parse_gcs_uri("events/a.json", "lake").uri == "gs://lake/events/a.json"


def test_mv_rejects_a_destination_inside_the_source():
    ctx = argparse.Namespace(settings=argparse.Namespace(bucket=None))
    for src, dst in (
        ("gs://bucket/a/", "gs://bucket/a/archive/"),
        ("gs://bucket/a/b.csv", "gs://bucket/a/b.csv"),
    ):
        ns = argparse.Namespace(src=src, dst=dst, recursive=True)
        with pytest.raises(UsageError):
            cmd_mv(ctx, ns)


def test_manifest_permissions():
    package = Path(__file__).parents[1] / "datus_gcs_plugin"
    manifest = yaml.safe_load((package / "datus-plugin.yml").read_text())
    assert manifest["cli"] == "datus_gcs_plugin.cli:main"
    assert "rm:*" in manifest["permissions"]["auto"]["ask"]


def test_installed_storage_sdk_supports_lifecycle_and_signed_urls():
    from google.cloud.storage import Blob, Bucket

    assert Bucket.lifecycle_rules.fset is not None
    assert hasattr(Blob, "generate_signed_url")
