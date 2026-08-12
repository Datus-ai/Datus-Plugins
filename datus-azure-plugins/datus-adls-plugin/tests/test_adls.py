import argparse
from pathlib import Path

import pytest
import yaml
from datus_azure_common import UsageError

from datus_adls_plugin.cli import _local_target, cmd_cp, cmd_sas
from datus_adls_plugin.config import Settings
from datus_adls_plugin.paths import parse_adls_uri

ACCOUNT_URL = "https://account.dfs.core.windows.net"


class _Downloader:
    """Minimal StorageStreamDownloader stand-in: streams, never buffers whole."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def readinto(self, sink) -> int:
        return sink.write(self._payload)


class _FakeFilesystem:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def get_paths(self, path=None, recursive=True):
        return [
            argparse.Namespace(name=name, is_directory=False, content_length=len(data))
            for name, data in self.objects.items()
        ]

    def get_file_client(self, key):
        return argparse.Namespace(
            download_file=lambda offset=None, length=None: _Downloader(
                self.objects[key]
            )
        )


def _ctx(objects: dict[str, bytes]):
    filesystem = _FakeFilesystem(objects)
    return argparse.Namespace(
        settings=Settings.from_profile({"account_url": ACCOUNT_URL}),
        filesystem=lambda name: filesystem,
    )


def test_paths_support_default_container():
    assert parse_adls_uri("events/a.json", "lake").uri == "abfss://lake/events/a.json"


def test_profile_accepts_an_entra_service_principal():
    settings = Settings.from_profile(
        {
            "account_url": ACCOUNT_URL,
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret",
        }
    )
    assert settings.azure.tenant_id == "tenant"
    assert settings.azure.client_secret == "secret"


def test_profile_accepts_a_non_public_cloud():
    settings = Settings.from_profile(
        {"account_url": ACCOUNT_URL, "cloud": "china", "max_attempts": "5"}
    )
    assert settings.azure.cloud == "china"
    assert settings.azure.authority == "https://login.chinacloudapi.cn"
    assert settings.azure.max_attempts == 5


def test_local_target_rejects_names_escaping_the_destination(tmp_path):
    assert _local_target(tmp_path, "a/b.csv") == (tmp_path / "a/b.csv").resolve()
    for name in ("../outside.csv", "a/../../outside.csv", "/etc/passwd"):
        with pytest.raises(UsageError):
            _local_target(tmp_path, name)


def test_recursive_download_streams_into_the_destination(tmp_path):
    ctx = _ctx({"events/a.csv": b"one", "events/nested/b.csv": b"two"})
    ns = argparse.Namespace(
        src="abfss://lake/events/", dst=str(tmp_path), recursive=True
    )
    assert cmd_cp(ctx, ns) == 0
    assert (tmp_path / "a.csv").read_bytes() == b"one"
    assert (tmp_path / "nested/b.csv").read_bytes() == b"two"


def test_recursive_download_refuses_a_traversing_object_name(tmp_path):
    ctx = _ctx({"events/../../escape.csv": b"bad"})
    ns = argparse.Namespace(
        src="abfss://lake/events/", dst=str(tmp_path / "out"), recursive=True
    )
    with pytest.raises(UsageError):
        cmd_cp(ctx, ns)
    assert not (tmp_path / "escape.csv").exists()


def test_sas_rejects_sas_token_profiles():
    settings = Settings.from_profile(
        {"account_url": ACCOUNT_URL, "sas_token": "?sv=2021&sig=x"}
    )
    ctx = argparse.Namespace(settings=settings)
    ns = argparse.Namespace(uri="abfss://lake/a.csv", permissions="r", expires=3600)
    with pytest.raises(UsageError):
        cmd_sas(ctx, ns)


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
