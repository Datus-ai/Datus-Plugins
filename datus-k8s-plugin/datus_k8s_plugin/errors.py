"""Plugin error hierarchy and conventional Datus CLI exit codes."""

EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_MISSING_DEPENDENCY = 8


class PluginError(Exception):
    exit_code = EXIT_RUNTIME


class UsageError(PluginError):
    exit_code = EXIT_USAGE


class ConfigError(PluginError):
    exit_code = EXIT_CONFIG


class MissingDependencyError(PluginError):
    exit_code = EXIT_MISSING_DEPENDENCY


class ApiError(PluginError):
    exit_code = EXIT_RUNTIME
