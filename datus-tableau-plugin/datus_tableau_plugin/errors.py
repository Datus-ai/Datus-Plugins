"""Stable CLI failure categories."""


class PluginError(Exception):
    exit_code = 1


class UsageError(PluginError):
    exit_code = 2


class ConfigError(PluginError):
    exit_code = 3


class MissingDependency(PluginError):
    exit_code = 8


class MetadataError(PluginError):
    """Metadata responses may contain errors even when HTTP succeeds."""
