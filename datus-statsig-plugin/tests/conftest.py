"""Shared fixtures: a fake requests.Session and a CLI runner around it."""

from __future__ import annotations

import json as jsonlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import pytest

from datus_statsig_plugin.cli import Context, build_parser
from datus_statsig_plugin.client import StatsigClient
from datus_statsig_plugin.config import Settings

BASE_URL = "https://statsig.test"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
        content_type: Optional[str] = None,
    ):
        self.status_code = status_code
        self._json = json_data
        if json_data is not None:
            self.text = jsonlib.dumps(json_data)
            content_type = content_type or "application/json"
        else:
            self.text = text
            content_type = content_type or "text/plain"
        self.headers = {"content-type": content_type}
        self.reason = "Error" if status_code >= 400 else "OK"

    @property
    def content(self) -> bytes:
        return self.text.encode()

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """Routes (METHOD, path) -> FakeResponse | [FakeResponse, ...] | callable(call)->FakeResponse."""

    def __init__(self):
        self.routes: Dict[tuple, Any] = {}
        self.calls: List[Dict[str, Any]] = []

    def add(self, method: str, path: str, response: Any) -> None:
        self.routes[(method.upper(), path)] = response

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = urlsplit(url).path
        call = {
            "method": method.upper(),
            "path": path,
            "params": jsonlib.loads(jsonlib.dumps(params)) if params is not None else None,
            "json": jsonlib.loads(jsonlib.dumps(json)) if json is not None else None,
            "headers": dict(headers or {}),
            "timeout": timeout,
        }
        self.calls.append(call)
        route = self.routes.get((method.upper(), path))
        if route is None:
            return FakeResponse(404, json_data={"message": f"no fake route for {method} {path}"})
        if isinstance(route, list):
            response = route.pop(0) if len(route) > 1 else route[0]
        elif callable(route):
            response = route(call)
        else:
            response = route
        # convenience: routes may be given as bare dicts (JSON bodies)
        if not isinstance(response, FakeResponse):
            response = FakeResponse(json_data=response)
        return response

    def calls_to(self, method: str, path: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method.upper() and c["path"] == path]


@pytest.fixture
def fake_session():
    return FakeSession()


@pytest.fixture
def settings():
    return Settings.from_profile(
        {"name": "test", "api_base_url": BASE_URL, "api_key": "test-console-key"}
    )


@pytest.fixture
def run_cli(fake_session, settings):
    """Parse argv with the real parser and run the handler against the fake session."""

    def _run(argv: List[str], settings_override: Optional[Settings] = None) -> int:
        parser = build_parser()
        ns = parser.parse_args(argv)
        active = settings_override or settings
        ctx = Context(active)
        ctx._client = StatsigClient(active, session=fake_session)
        rc = ns.func(ctx, ns)
        return 0 if rc is None else rc

    return _run


def single(data: Any) -> dict:
    return {"message": "ok", "data": data}


def paged(items: list, next_page=None) -> dict:
    return {"message": "ok", "data": items, "pagination": {"nextPage": next_page}}
