from pathlib import Path

import yaml

from datus_adls_plugin.paths import parse_adls_uri


def test_paths_support_default_container():
    assert parse_adls_uri("events/a.json", "lake").uri == "abfss://lake/events/a.json"


def test_manifest_sensitive_operations_ask():
    package = Path(__file__).parents[1] / "datus_adls_plugin"
    manifest = yaml.safe_load((package / "datus-plugin.yml").read_text())
    assert manifest["cli"] == "datus_adls_plugin.cli:main"
    assert "sas:*" in manifest["permissions"]["auto"]["ask"]


def test_installed_adls_sdk_exposes_wrapped_operations():
    from azure.storage.blob import BlobSasPermissions
    from azure.storage.filedatalake import DataLakeFileClient

    assert hasattr(DataLakeFileClient, "upload_data")
    assert hasattr(DataLakeFileClient, "get_access_control")
    assert hasattr(BlobSasPermissions, "from_string")
