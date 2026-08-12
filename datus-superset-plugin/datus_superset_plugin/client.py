"""Superset REST client with login/token auth, refresh, and CSRF handling."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .errors import ApiError, UsageError


class SupersetClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout,
            verify=settings.verify_ssl,
            follow_redirects=False,
            transport=transport,
        )
        self._access_token = settings.access_token
        self._refresh_token: str | None = None
        self._csrf_token: str | None = None

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        raw: bool = False,
    ) -> Any:
        normalized = _safe_relative_path(path, allow_non_v1=not raw)
        method = method.upper()
        self._ensure_auth()
        headers = self._headers(method)
        response = self._client.request(
            method,
            normalized,
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=json_body,
            data=data,
            files=files,
            headers=headers,
        )
        if response.status_code == 401 and self._refresh_token:
            self._refresh()
            headers = self._headers(method)
            response = self._client.request(
                method, normalized, params=params, json=json_body, data=data, files=files, headers=headers
            )
        if response.is_redirect:
            raise ApiError(f"refusing redirect for {method} {normalized}", status_code=response.status_code)
        if response.status_code >= 400:
            raise ApiError(
                f"HTTP {response.status_code} for {method} {normalized}: {_detail(response)}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.content if "text" not in response.headers.get("content-type", "") else response.text

    def _ensure_auth(self) -> None:
        if self._access_token:
            return
        payload = {
            "username": self.settings.username,
            "password": self.settings.password,
            "provider": self.settings.provider,
            "refresh": True,
        }
        response = self._client.post("/api/v1/security/login", json=payload)
        if response.status_code >= 400:
            raise ApiError(f"Superset login failed: {_detail(response)}", status_code=response.status_code)
        body = response.json()
        self._access_token = body.get("access_token")
        self._refresh_token = body.get("refresh_token")
        if not self._access_token:
            raise ApiError("Superset login response did not contain access_token")

    def _refresh(self) -> None:
        response = self._client.post(
            "/api/v1/security/refresh",
            headers={"Authorization": f"Bearer {self._refresh_token}"},
        )
        if response.status_code >= 400:
            raise ApiError(f"Superset token refresh failed: {_detail(response)}", status_code=response.status_code)
        self._access_token = response.json().get("access_token")

    def _headers(self, method: str) -> dict[str, str]:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._access_token}"}
        if method not in {"GET", "HEAD", "OPTIONS"}:
            if not self._csrf_token:
                response = self._client.get(
                    "/api/v1/security/csrf_token/",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                )
                if response.status_code >= 400:
                    raise ApiError(f"failed to obtain CSRF token: {_detail(response)}")
                self._csrf_token = response.json().get("result")
            headers["X-CSRFToken"] = self._csrf_token or ""
            headers["Referer"] = self.settings.base_url + "/"
        return headers


def _safe_relative_path(path: str, *, allow_non_v1: bool = False) -> str:
    value = str(path or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise UsageError("API path must be relative to the configured Superset instance")
    if ".." in parsed.path.split("/"):
        raise UsageError("API path may not contain '..'")
    normalized = "/" + parsed.path.lstrip("/")
    if not allow_non_v1 and not normalized.startswith("/api/v1/"):
        raise UsageError("raw API paths must start with /api/v1/")
    if allow_non_v1 and not normalized.startswith(("/api/v1/", "/superset/", "/health")):
        raise UsageError("unsupported Superset API path")
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
