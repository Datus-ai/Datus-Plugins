"""Validate the plain profile dict passed to the plugin by Datus."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError, UsageError

_DURATION = re.compile(r"^(?:0|[1-9]\d*(?:\.\d+)?(?:ms|s|m|h))$")
_NAMESPACE = re.compile(
    r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$"
)
KNOWN_KEYS = {
    "name",
    "default",
    "kubeconfig",
    "context",
    "provider",
    "provider_profile",
    "provider_config",
    "namespace",
    "allowed_namespaces",
    "request_timeout",
    "field_manager",
}


def _csv(raw: Any) -> tuple[str, ...]:
    values = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def duration_seconds(raw: str) -> float:
    value = str(raw).strip()
    if not _DURATION.fullmatch(value):
        raise ConfigError(
            f"request_timeout must be 0 or a duration such as 500ms, 30s, 2m, or 1h (got {raw!r})"
        )
    if value == "0":
        return 0.0
    units = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    unit = "ms" if value.endswith("ms") else value[-1]
    return float(value[: -len(unit)]) * units[unit]


@dataclass(frozen=True)
class Settings:
    profile_name: str
    kubeconfig: str | None
    context: str | None
    namespace: str
    allowed_namespaces: tuple[str, ...]
    provider: str | None = None
    provider_profile: str | None = None
    provider_config: str | None = None
    request_timeout: str = "30s"
    field_manager: str = "datus-k8s"

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "Settings":
        data = dict(profile or {})
        unknown = set(data) - KNOWN_KEYS
        if unknown:
            raise ConfigError(f"unknown k8s profile field(s): {', '.join(sorted(unknown))}")

        profile_name = str(data.get("name") or "").strip()
        kubeconfig = str(data.get("kubeconfig") or "").strip() or None
        provider = str(data.get("provider") or "").strip() or None
        provider_profile = str(data.get("provider_profile") or "").strip() or None
        provider_config = str(data.get("provider_config") or "").strip() or None
        context = str(data.get("context") or "").strip() or None
        if bool(kubeconfig) == bool(provider):
            raise ConfigError(
                "configure exactly one of kubeconfig or provider under "
                "agent.plugins.k8s.<profile>; "
                "run the k8s-setup skill for guided configuration"
            )
        if provider:
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", provider):
                raise ConfigError(f"invalid cloud provider plugin name: {provider!r}")
            provider_profile = provider_profile or profile_name
            if not provider_profile:
                raise ConfigError(
                    "provider_profile is required when the k8s profile name is unavailable"
                )
            if context:
                raise ConfigError("context is only valid with kubeconfig")
        elif provider_profile:
            raise ConfigError("provider_profile requires provider")
        if provider_config and not provider:
            raise ConfigError("provider_config requires provider")
        namespace = str(data.get("namespace") or "default").strip()
        allowed = _csv(data.get("allowed_namespaces") or namespace)
        if not allowed:
            raise ConfigError("allowed_namespaces must contain at least one namespace")
        for item in (namespace, *allowed):
            if len(item) > 253 or not _NAMESPACE.fullmatch(item):
                raise ConfigError(f"invalid Kubernetes namespace: {item!r}")
        if namespace not in allowed:
            raise ConfigError(
                f"default namespace {namespace!r} is not present in allowed_namespaces"
            )

        timeout = str(data.get("request_timeout") or "30s").strip()
        duration_seconds(timeout)
        field_manager = str(data.get("field_manager") or "datus-k8s").strip()
        if not field_manager:
            raise ConfigError("field_manager may not be empty")
        return cls(
            profile_name=profile_name,
            kubeconfig=kubeconfig,
            context=context if kubeconfig else None,
            namespace=namespace,
            allowed_namespaces=allowed,
            provider=provider,
            provider_profile=provider_profile,
            provider_config=provider_config,
            request_timeout=timeout,
            field_manager=field_manager,
        )

    def resolve_kubeconfig(self, cwd: Path | None = None) -> Path:
        if not self.kubeconfig:
            raise ConfigError("this managed Kubernetes profile has no kubeconfig")
        base = (cwd or Path.cwd()).resolve()
        candidate = Path(self.kubeconfig).expanduser()
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
            if not candidate.is_relative_to(base):
                raise ConfigError(
                    f"relative kubeconfig escapes the current project directory: {self.kubeconfig!r}"
                )
        else:
            candidate = candidate.resolve()
        if not candidate.exists():
            raise ConfigError(f"kubeconfig does not exist: {candidate}")
        if not candidate.is_file():
            raise ConfigError(f"kubeconfig is not a regular file: {candidate}")
        try:
            with candidate.open("rb"):
                pass
        except OSError as exc:
            raise ConfigError(f"kubeconfig is not readable: {candidate}: {exc}") from exc
        return candidate

    @property
    def managed(self) -> bool:
        return bool(self.provider)

    def check_namespace(self, value: str | None) -> str:
        namespace = str(value or self.namespace).strip()
        if namespace not in self.allowed_namespaces:
            raise UsageError(
                f"namespace {namespace!r} is outside this profile; allowed namespaces: "
                f"{', '.join(self.allowed_namespaces)}"
            )
        return namespace

    @property
    def timeout_seconds(self) -> float:
        return duration_seconds(self.request_timeout)
