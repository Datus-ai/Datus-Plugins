"""Shared fixtures: a duck-typed fake boto3 client and a CLI runner.

The plugin builds boto3 clients lazily through ``AwsContext.client(service)``;
tests bypass boto3 entirely by pre-populating ``ctx._clients[service]`` with a
``FakeBotoClient``. This mirrors how datus-airflow-plugin injects a fake
``requests.Session``.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from datus_aws_common import AwsContext, run
from datus_cloudwatch_plugin.cli import build_parser
from datus_cloudwatch_plugin.config import Settings


class FakeBotoClient:
    """Records calls and returns routed responses.

    - ``set(method, response)`` routes a direct call (response may be a value, a
      list consumed one-per-call, or a callable(kwargs) -> value).
    - ``set_pages(method, pages)`` makes ``method`` paginable, so the shared
      ``paginate`` helper walks ``pages``.
    """

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
        # only reached for boto3 operation names (real attrs resolve normally)
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
    return {"logs": FakeBotoClient(), "cloudwatch": FakeBotoClient()}


@pytest.fixture
def run_cli(clients):
    """Run argv through the real dispatch (so PluginError maps to an exit code)
    but against pre-injected fake clients."""

    def _run(argv: List[str]) -> int:
        ctx = AwsContext(Settings.from_profile({"region": "us-east-1"}))
        ctx._clients.update(clients)
        return run(build_parser(), argv, lambda: ctx)

    return _run
