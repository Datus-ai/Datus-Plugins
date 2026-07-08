"""Minimal Statsig Console API client (version 20240601).

Auth is a single header (``STATSIG-API-KEY``); the API version travels in
``STATSIG-API-VERSION``. All Console endpoints live under ``/console/v1``.
List endpoints share one envelope: ``{message, data: [...], pagination: {...}}``
where ``pagination.nextPage`` drives page walking.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests

from .config import Settings
from .errors import ApiError

API_PREFIX = "/console/v1"
# Statsig's default page size; kept modest so paginate() works against any project.
PAGE_SIZE = 100


class StatsigClient:
    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/")
        self._session = session or requests.Session()

    def _api_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{API_PREFIX}{path}"

    def _headers(self) -> Dict[str, str]:
        return {
            "STATSIG-API-KEY": self.settings.require_api_key(),
            "STATSIG-API-VERSION": self.settings.api_version,
            "Accept": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
    ) -> Any:
        url = self._api_url(path)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self._session.request(
            method,
            url,
            params=clean_params or None,
            json=json_body,
            headers=self._headers(),
            timeout=self.settings.timeout,
        )
        if resp.status_code == 401:
            raise ApiError(
                f"HTTP 401 for {method} {path}: the Console API key was rejected — "
                f"{_extract_detail(resp)}",
                status_code=401,
            )
        if resp.status_code == 429:
            raise ApiError(
                f"HTTP 429 for {method} {path}: rate limited (mutations are capped at "
                f"~100/10s and ~900/15min per project) — {_extract_detail(resp)}",
                status_code=429,
            )
        if resp.status_code >= 400:
            raise ApiError(
                f"HTTP {resp.status_code} for {method} {path}: {_extract_detail(resp)}",
                status_code=resp.status_code,
            )
        if resp.status_code == 204 or not resp.content:
            return None
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return resp.json()
        return resp.text

    def paginate(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Collect the ``data`` array across pages (or the first ``limit`` items).

        Follows ``pagination.nextPage``; tolerates endpoints that omit the
        pagination block (single-shot lists) by stopping after the first page.
        """
        collected: List[Dict[str, Any]] = []
        page = 1
        while True:
            page_params = {**(params or {}), "page": page, "limit": PAGE_SIZE}
            resp = self.request("GET", path, params=page_params)
            items = (resp or {}).get("data") or []
            collected.extend(items)
            if limit is not None and len(collected) >= limit:
                break
            if not items:
                break
            next_page = ((resp or {}).get("pagination") or {}).get("nextPage")
            if not next_page:
                break
            page = int(next_page)
        return collected[:limit] if limit is not None else collected


def _extract_detail(resp: requests.Response) -> str:
    """Flatten Statsig error payloads ({"message": ...} / {"errors": [...]}) to one line."""
    try:
        data = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return text[:500] if text else (resp.reason or "no error detail")
    if isinstance(data, dict):
        for key in ("message", "detail", "error"):
            val = data.get(key)
            if isinstance(val, str) and val:
                errors = data.get("errors")
                if isinstance(errors, list) and errors:
                    return f"{val}: " + "; ".join(str(e) for e in errors)
                return val
        return json.dumps(data, ensure_ascii=False)[:500]
    return str(data)[:500]
