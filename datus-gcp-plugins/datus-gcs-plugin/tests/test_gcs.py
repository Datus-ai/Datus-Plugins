from pathlib import Path

import yaml

from datus_gcs_plugin.paths import parse_gcs_uri


def test_paths_support_default_bucket():
    assert parse_gcs_uri("events/a.json", "lake").uri == "gs://lake/events/a.json"


def test_manifest_permissions():
    package = Path(__file__).parents[1] / "datus_gcs_plugin"
    manifest = yaml.safe_load((package / "datus-plugin.yml").read_text())
    assert manifest["cli"] == "datus_gcs_plugin.cli:main"
    assert "rm:*" in manifest["permissions"]["auto"]["ask"]


def test_installed_storage_sdk_supports_lifecycle_and_signed_urls():
    from google.cloud.storage import Blob, Bucket

    assert Bucket.lifecycle_rules.fset is not None
    assert hasattr(Blob, "generate_signed_url")
