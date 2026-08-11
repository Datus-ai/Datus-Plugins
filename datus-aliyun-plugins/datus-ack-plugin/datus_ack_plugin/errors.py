from __future__ import annotations


class PluginError(Exception):
    exit_code = 1


class UsageError(PluginError):
    exit_code = 2


class ConfigError(PluginError):
    exit_code = 3


class MissingDependencyError(PluginError):
    exit_code = 8


class ApiError(PluginError):
    pass
