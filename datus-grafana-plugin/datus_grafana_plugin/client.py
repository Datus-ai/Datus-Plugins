"""Grafana HTTP client with guarded same-origin paths and API capability fallback."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .errors import ApiError, UsageError


class GrafanaClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        auth = None
        if settings.auth_mode == "token":
            headers["Authorization"] = f"Bearer {settings.token}"
        else:
            auth = (settings.username or "", settings.password or "")
        if settings.org_id:
            headers["X-Grafana-Org-Id"] = settings.org_id
        self._client = httpx.Client(
            base_url=settings.base_url, headers=headers, auth=auth,
            timeout=settings.timeout, verify=settings.verify_ssl,
            follow_redirects=False, transport=transport,
        )
        self._major: int | None = None

    def close(self) -> None:
        self._client.close()

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json_body: Any = None, raw: bool = False,
    ) -> Any:
        # ``raw`` documents that this call came through the guarded escape
        # hatch. The same path validator applies to typed and raw calls.
        del raw
        normalized = safe_path(path)
        response = self._client.request(method.upper(), normalized, params=params, json=json_body)
        if response.is_redirect:
            raise ApiError(f"refusing redirect for {method.upper()} {normalized}", status_code=response.status_code)
        if response.status_code >= 400:
            raise ApiError(
                f"HTTP {response.status_code} for {method.upper()} {normalized}: {_detail(response)}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.content if "text" not in response.headers.get("content-type", "") else response.text

    def dashboard_request(
        self, method: str, uid: str | None = None, *, json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        use_new = self.settings.api_mode == "new" or (
            self.settings.api_mode == "auto" and self.major_version() >= 12
        )
        if use_new:
            base = f"/apis/dashboard.grafana.app/v1beta1/namespaces/{self.settings.namespace}/dashboards"
            path = base + (f"/{uid}" if uid else "")
            try:
                return self.request(method, path, params=params, json_body=json_body)
            except ApiError as exc:
                if self.settings.api_mode != "auto" or exc.status_code not in {404, 405}:
                    raise
        if method.upper() == "GET" and uid:
            return self.request("GET", f"/api/dashboards/uid/{uid}")
        if method.upper() == "DELETE" and uid:
            return self.request("DELETE", f"/api/dashboards/uid/{uid}")
        return self.request("POST", "/api/dashboards/db", json_body=json_body)

    def major_version(self) -> int:
        if self._major is None:
            body = self.request("GET", "/api/health") or {}
            raw = str(body.get("version") or "0") if isinstance(body, dict) else "0"
            try:
                self._major = int(raw.split(".", 1)[0])
            except ValueError:
                self._major = 0
        return self._major


def safe_path(path: str) -> str:
    parsed = urlsplit(str(path or "").strip())
    if parsed.scheme or parsed.netloc:
        raise UsageError("API path must be relative to the configured Grafana instance")
    if ".." in parsed.path.split("/"):
        raise UsageError("API path may not contain '..'")
    normalized = "/" + parsed.path.lstrip("/")
    if not normalized.startswith(("/api/", "/apis/")):
        raise UsageError("Grafana API paths must start with /api/ or /apis/")
    return normalized + (f"?{parsed.query}" if parsed.query else "")


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or response.reason_phrase)[:500]
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            if data.get(key):
                return str(data[key])[:500]
    return json.dumps(data, ensure_ascii=False, default=str)[:500]
