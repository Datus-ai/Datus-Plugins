"""Shared fixtures: a duck-typed fake boto3 client and a CLI runner."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from datus_aws_common import AwsContext, run
from datus_emr_serverless_plugin.cli import build_parser
from datus_emr_serverless_plugin.config import Settings


class FakeBotoClient:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self._responses: Dict[str, Any] = {}
        self._pages: Dict[str, Any] = {}

    def set(self, method: str, response: Any) -> "FakeBotoClient":
        self._responses[method] = response
        return self

    def set_pages(self, method: str, pages: Any) -> "FakeBotoClient":
        self._pages[method] = pages
        return self

    def can_paginate(self, method: str) -> bool:
        return method in self._pages

    def get_paginator(self, method: str):
        pages = self._pages[method]
        recorder = self

        class _Paginator:
            def paginate(self, **kwargs):
                recorder.calls.append({"method": method, "kwargs": kwargs})
                return iter(pages)

        return _Paginator()

    def calls_to(self, method: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method]

    def __getattr__(self, method: str):
        def _call(**kwargs):
            self.calls.append({"method": method, "kwargs": kwargs})
            resp = self._responses.get(method)
            if resp is None:
                return {}
            if isinstance(resp, list):
                return resp.pop(0) if len(resp) > 1 else resp[0]
            if callable(resp):
                return resp(kwargs)
            return resp

        return _call


@pytest.fixture
def clients():
    return {"emr-serverless": FakeBotoClient()}


@pytest.fixture
def run_cli(clients):
    def _run(argv: List[str], profile: Optional[Dict[str, Any]] = None) -> int:
        ctx = AwsContext(Settings.from_profile(profile or {"region": "us-east-1"}))
        ctx._clients.update(clients)
        return run(build_parser(), argv, lambda: ctx)

    return _run
