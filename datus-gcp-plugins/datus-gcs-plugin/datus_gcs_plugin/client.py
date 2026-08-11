from __future__ import annotations

from typing import Any

from datus_gcp_common import MissingDependencyError, build_credentials

from .config import Settings


class GcsContext:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any = None

    @property
    def client(self):
        if self._client is None:
            try:
                from google.api_core.client_options import ClientOptions
                from google.cloud import storage
            except ImportError as exc:
                raise MissingDependencyError(
                    "google-cloud-storage is required for the GCS plugin"
                ) from exc
            credentials, project = build_credentials(self.settings.gcp)
            options = (
                ClientOptions(api_endpoint=self.settings.gcp.api_endpoint)
                if self.settings.gcp.api_endpoint
                else None
            )
            self._client = storage.Client(
                project=project, credentials=credentials, client_options=options
            )
        return self._client
