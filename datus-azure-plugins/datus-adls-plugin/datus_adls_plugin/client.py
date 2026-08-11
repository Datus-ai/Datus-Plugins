from __future__ import annotations

from typing import Any

from datus_azure_common import MissingDependencyError, build_credential

from .config import Settings


class AdlsContext:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._credential: Any = None
        self._client: Any = None

    @property
    def credential(self):
        if self._credential is None:
            if self.settings.account_key:
                try:
                    from azure.core.credentials import AzureNamedKeyCredential
                except ImportError as exc:
                    raise MissingDependencyError(
                        "azure-core is required for shared-key authentication"
                    ) from exc
                self._credential = AzureNamedKeyCredential(
                    self.settings.account_name, self.settings.account_key
                )
            elif self.settings.sas_token:
                self._credential = self.settings.sas_token
            else:
                self._credential = build_credential(self.settings.azure)
        return self._credential

    @property
    def client(self):
        if self._client is None:
            try:
                from azure.storage.filedatalake import DataLakeServiceClient
            except ImportError as exc:
                raise MissingDependencyError(
                    "azure-storage-file-datalake is required for the ADLS plugin"
                ) from exc
            self._client = DataLakeServiceClient(
                account_url=self.settings.account_url, credential=self.credential
            )
        return self._client

    def filesystem(self, name: str):
        return self.client.get_file_system_client(name)
