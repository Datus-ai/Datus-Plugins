"""Exit codes and exception hierarchy shared by all Datus AWS plugins.

Copied byte-for-byte from datus-airflow-plugin's errors.py (it is domain
neutral); only ``ApiError``'s docstring is generalised from Airflow to AWS.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_MISSING_DEPENDENCY = 8


class PluginError(Exception):
    """Base error: message is printed to stderr, exit_code becomes the CLI exit code."""

    exit_code = EXIT_RUNTIME


class UsageError(PluginError):
    exit_code = EXIT_USAGE


class ConfigError(PluginError):
    exit_code = EXIT_CONFIG


class MissingDependencyError(PluginError):
    exit_code = EXIT_MISSING_DEPENDENCY


class ApiError(PluginError):
    """A failure calling an AWS API (mapped from botocore ClientError/BotoCoreError)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
