EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3


class PluginError(Exception):
    exit_code = EXIT_RUNTIME


class UsageError(PluginError):
    exit_code = EXIT_USAGE


class ConfigError(PluginError):
    exit_code = EXIT_CONFIG


class ApiError(PluginError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
