from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("datus-plugin-e2e")
    group.addoption("--workflow", action="store", help="Workflow directory name under tests/e2e/workflows")
    group.addoption("--run-config", action="store", help="Ephemeral YAML containing agent ref, config, and plugin root")
    group.addoption("--run-live", action="store_true", default=False, help="Provision services and call the configured LLM")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
