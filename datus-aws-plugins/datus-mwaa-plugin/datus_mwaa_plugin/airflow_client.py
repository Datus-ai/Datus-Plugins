"""Authenticated access to the Airflow REST API exposed by Amazon MWAA.

MWAA issues a short-lived web login token. Posting that token to
``/aws_mwaa/login`` establishes the web session used for the stable Airflow
``/api/v1`` endpoints. This client deliberately contains no S3 operations;
DAG source is read from Airflow's ``dagSources`` API.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests

from datus_aws_common import ApiError, call

PAGE_SIZE = 50


def _detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or response.reason or "no error detail").strip()[:500]
    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)[:500]


class MwaaAirflowClient:
    """Airflow v1 client backed by a short-lived MWAA web session."""

    def __init__(
        self,
        mwaa_client: Any,
        environment: str,
        *,
        timeout: float = 60.0,
        session: Optional[requests.Session] = None,
    ):
        self._mwaa = mwaa_client
        self.environment = environment
        self.timeout = timeout
        self._session = session or requests.Session()
        self._hostname: Optional[str] = None
        self._logged_in = False

    def _login(self) -> None:
        token = call(self._mwaa.create_web_login_token, Name=self.environment)
        hostname = token.get("WebServerHostname")
        web_token = token.get("WebToken")
        if not hostname or not web_token:
            raise ApiError(
                f"MWAA returned no web login token for environment {self.environment!r}"
            )
        try:
            response = self._session.request(
                "POST",
                f"https://{hostname}/aws_mwaa/login",
                data={"token": web_token},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(f"cannot establish MWAA Airflow web session: {exc}") from exc
        if response.status_code >= 400:
            raise ApiError(
                f"MWAA Airflow login failed (HTTP {response.status_code}): {_detail(response)}",
                status_code=response.status_code,
            )
        self._hostname = str(hostname)
        self._logged_in = True

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        accept: str = "application/json",
    ) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        for attempt in (1, 2):
            if not self._logged_in:
                self._login()
            assert self._hostname is not None
            clean_params = {key: value for key, value in (params or {}).items() if value is not None}
            try:
                response = self._session.request(
                    method,
                    f"https://{self._hostname}/api/v1{path}",
                    params=clean_params or None,
                    headers={"Accept": accept},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise ApiError(f"cannot reach MWAA Airflow API: {exc}") from exc
            if response.status_code == 401 and attempt == 1:
                self._logged_in = False
                continue
            break
        if response.status_code >= 400:
            raise ApiError(
                f"MWAA Airflow API HTTP {response.status_code} for {method} {path}: "
                f"{_detail(response)}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.text

    def paginate(
        self,
        path: str,
        items_key: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page_limit = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(rows))
            if page_limit <= 0:
                break
            page = self.request(
                "GET",
                path,
                params={**(params or {}), "limit": page_limit, "offset": offset},
            )
            items = (page or {}).get(items_key) or []
            rows.extend(items)
            offset += len(items)
            total = (page or {}).get("total_entries")
            if not items or (limit is not None and len(rows) >= limit):
                break
            if total is not None and offset >= int(total):
                break
            if total is None and len(items) < page_limit:
                break
        return rows[:limit] if limit is not None else rows
