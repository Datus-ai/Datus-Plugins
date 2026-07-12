"""Shared plumbing for Datus AWS plugins.

Plugins import from here rather than copying: error types + exit codes, output
rendering, ``AwsSettings`` + key validation, boto3 session/client builders, the
call/paginate/wait_until helpers, and CLI helpers (``AwsContext``, ``run``, ...).
"""

from __future__ import annotations

from .awsconfig import (
    AWS_KEYS,
    AwsSettings,
    aws_config_schema,
    validate_aws_profile,
    validate_keys,
)
from .cli import (
    AwsContext,
    add_output_option,
    confirm,
    parse_datetime_arg,
    parse_json_arg,
    run,
    summarize_aws_profile,
)
from .client import call, eprint, paginate, wait_until
from .errors import (
    EXIT_CONFIG,
    EXIT_MISSING_DEPENDENCY,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USAGE,
    ApiError,
    ConfigError,
    MissingDependencyError,
    PluginError,
    UsageError,
)
from .output import DEFAULT_FORMAT, FORMATS, render_one, render_rows
from .session import build_client, build_session

__all__ = [
    "AWS_KEYS",
    "AwsSettings",
    "aws_config_schema",
    "validate_aws_profile",
    "validate_keys",
    "AwsContext",
    "add_output_option",
    "confirm",
    "parse_datetime_arg",
    "parse_json_arg",
    "run",
    "summarize_aws_profile",
    "call",
    "eprint",
    "paginate",
    "wait_until",
    "EXIT_CONFIG",
    "EXIT_MISSING_DEPENDENCY",
    "EXIT_OK",
    "EXIT_RUNTIME",
    "EXIT_USAGE",
    "ApiError",
    "ConfigError",
    "MissingDependencyError",
    "PluginError",
    "UsageError",
    "DEFAULT_FORMAT",
    "FORMATS",
    "render_one",
    "render_rows",
    "build_client",
    "build_session",
]
