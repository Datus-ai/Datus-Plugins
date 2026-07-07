"""Minimal Airflow REST API v2 client (Airflow 3.x).

Authentication follows the Airflow 3 model: either a static JWT provided in
the profile, or username/password exchanged for a JWT at ``POST /auth/token``
(the endpoint exposed by both SimpleAuthManager and FabAuthManager).
Fetched tokens are cached on disk (0600) and refreshed transparently on 401.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

from .config import Settings
from .errors import ApiError, ConfigError

API_PREFIX = "/api/v2"
# Matches the server-side default page size; never exceeds the default
# [api] maximum_page_limit, so pagination works against stock servers.
PAGE_SIZE = 50
_EXPIRY_SKEW_SECONDS = 30


def _jwt_expiry(token: str) -> Optional[int]:
    """Best-effort read of the ``exp`` claim; None for opaque tokens."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


class AirflowClient:
    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.settings = settings
        self.base_url = settings.require_base_url()
        self._session = session or requests.Session()
        self._static_token = bool(settings.token)
        self._token: Optional[str] = settings.token

    # ------------------------------------------------------------------ auth

    def _can_login(self) -> bool:
        return bool(self.settings.username and self.settings.password is not None)

    def _cache_path(self) -> Optional[str]:
        if not self.settings.cache_token:
            return None
        digest = hashlib.sha256(
            f"{self.base_url}|{self.settings.username or ''}".encode()
        ).hexdigest()[:16]
        return str(self.settings.resolved_cache_dir() / f"token-{digest}.json")

    def _token_usable(self, token: str) -> bool:
        exp = _jwt_expiry(token)
        return exp is None or exp > time.time() + _EXPIRY_SKEW_SECONDS

    def _load_cached_token(self) -> Optional[str]:
        path = self._cache_path()
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                token = json.load(fh).get("access_token")
        except (OSError, ValueError):
            return None
        if token and self._token_usable(token):
            return token
        return None

    def _store_cached_token(self, token: str) -> None:
        path = self._cache_path()
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"access_token": token}, fh)
        except OSError:
            pass  # caching is best-effort; auth still works without it

    def _drop_cached_token(self) -> None:
        path = self._cache_path()
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def _login(self) -> str:
        if not self._can_login():
            raise ConfigError(
                "no valid credentials: configure either `token` or "
                "`username` + `password` for this profile"
            )
        url = self.settings.resolved_auth_token_url()
        try:
            resp = self._session.request(
                "POST",
                url,
                json={"username": self.settings.username, "password": self.settings.password},
                headers={"Accept": "application/json"},
                timeout=self.settings.timeout,
                verify=self.settings.verify_ssl,
            )
        except requests.RequestException as exc:
            raise ApiError(f"cannot reach auth endpoint {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ApiError(
                f"login failed at {url} (HTTP {resp.status_code}): "
                f"{_extract_detail(resp)} — check username/password; the server's "
                "auth manager must expose POST /auth/token",
                status_code=resp.status_code,
            )
        token = (resp.json() or {}).get("access_token")
        if not token:
            raise ApiError(f"auth endpoint {url} returned no access_token")
        self._store_cached_token(token)
        return token

    def _get_token(self) -> Optional[str]:
        if self._token and (self._static_token or self._token_usable(self._token)):
            return self._token
        if self._static_token:
            return self._token
        cached = self._load_cached_token()
        if cached:
            self._token = cached
            return cached
        if self._can_login():
            self._token = self._login()
            return self._token
        return None  # unauthenticated servers do exist (e.g. auth disabled)

    # -------------------------------------------------------------- requests

    def _api_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{API_PREFIX}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        accept: str = "application/json",
    ) -> Any:
        url = self._api_url(path)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        resp: Optional[requests.Response] = None
        for attempt in (1, 2):
            headers = {"Accept": accept}
            token = self._get_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = self._session.request(
                method,
                url,
                params=clean_params or None,
                json=json_body,
                headers=headers,
                timeout=self.settings.timeout,
                verify=self.settings.verify_ssl,
            )
            if resp.status_code == 401 and attempt == 1 and not self._static_token and self._can_login():
                # cached/held token expired server-side: drop it and re-login once
                self._drop_cached_token()
                self._token = None
                continue
            break

        assert resp is not None
        if resp.status_code == 401:
            hint = (
                "the configured token was rejected"
                if self._static_token
                else "authentication failed"
            )
            raise ApiError(
                f"HTTP 401 for {method} {path}: {hint} — {_extract_detail(resp)}",
                status_code=401,
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
        items_key: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Collect every page of a list endpoint (or the first `limit` items)."""
        collected: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page_limit = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(collected))
            if page_limit <= 0:
                break
            page = self.request(
                "GET",
                path,
                params={**(params or {}), "limit": page_limit, "offset": offset},
            )
            items = (page or {}).get(items_key) or []
            collected.extend(items)
            offset += len(items)
            total = (page or {}).get("total_entries")
            if not items:
                break
            if limit is not None and len(collected) >= limit:
                break
            if total is not None and offset >= int(total):
                break
            if total is None and len(items) < page_limit:
                break
        if limit is not None:
            return collected[:limit]
        return collected


def _extract_detail(resp: requests.Response) -> str:
    """Flatten FastAPI error payloads ({"detail": str | list | dict}) to one line."""
    try:
        data = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return text[:500] if text else resp.reason or "no error detail"
    detail = data.get("detail", data) if isinstance(data, dict) else data
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts: Iterable[str] = (
            f"{'.'.join(str(x) for x in item.get('loc', []))}: {item.get('msg', item)}"
            if isinstance(item, dict)
            else str(item)
            for item in detail
        )
        return "; ".join(parts)
    return json.dumps(detail, ensure_ascii=False)[:500]
