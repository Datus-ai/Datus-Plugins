"""Strict loaders for the checked-in workflow and ephemeral run contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ContractError(ValueError):
    """Raised when a workflow or run configuration is unsafe or ambiguous."""


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be a mapping")
    return value


def _strings(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ContractError(f"{where} must be a list of non-empty strings")
    return list(value)


def _only(data: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ContractError(f"{where} contains unknown fields: {', '.join(unknown)}")


@dataclass(frozen=True)
class PluginSpec:
    distribution: str
    path: str
    name: str
    profile: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, value: Any, where: str) -> "PluginSpec":
        data = _mapping(value, where)
        _only(data, {"distribution", "path", "name", "profile"}, where)
        required = ("distribution", "path", "name")
        for key in required:
            if not isinstance(data.get(key), str) or not data[key]:
                raise ContractError(f"{where}.{key} must be a non-empty string")
        profile = data.get("profile") or {}
        _mapping(profile, f"{where}.profile")
        return cls(data["distribution"], data["path"], data["name"], profile)


@dataclass(frozen=True)
class OracleSpec:
    type: str
    config: dict[str, Any]

    @classmethod
    def parse(cls, value: Any, where: str) -> "OracleSpec":
        data = _mapping(value, where)
        _only(data, {"type", "config"}, where)
        if not isinstance(data.get("type"), str) or not data["type"]:
            raise ContractError(f"{where}.type must be a non-empty string")
        config = data.get("config") or {}
        _mapping(config, f"{where}.config")
        return cls(data["type"], config)


@dataclass(frozen=True)
class Workflow:
    path: Path
    name: str
    description: str
    target: PluginSpec
    support_plugins: tuple[PluginSpec, ...]
    prompt_file: str
    timeout_seconds: int
    environment: dict[str, Any]
    seed: str | None
    outputs: tuple[str, ...]
    bash_allow: tuple[str, ...]
    oracles: tuple[OracleSpec, ...]
    efficiency: dict[str, Any]
    cleanup: dict[str, Any]
    tags: tuple[str, ...]

    @classmethod
    def parse(cls, raw: Any, path: Path) -> "Workflow":
        data = _mapping(raw, str(path))
        _only(data, {"apiVersion", "kind", "metadata", "spec"}, str(path))
        if data.get("apiVersion") != "datus.ai/v1alpha1":
            raise ContractError(f"{path}: apiVersion must be datus.ai/v1alpha1")
        if data.get("kind") != "PluginE2EWorkflow":
            raise ContractError(f"{path}: kind must be PluginE2EWorkflow")

        metadata = _mapping(data.get("metadata"), f"{path}.metadata")
        _only(metadata, {"name", "description", "tags"}, f"{path}.metadata")
        name = metadata.get("name")
        if not isinstance(name, str) or not name:
            raise ContractError(f"{path}.metadata.name must be a non-empty string")
        description = metadata.get("description") or ""
        if not isinstance(description, str):
            raise ContractError(f"{path}.metadata.description must be a string")

        spec = _mapping(data.get("spec"), f"{path}.spec")
        _only(
            spec,
            {"target", "supportPlugins", "agent", "environment", "workspace", "permissions", "oracles", "efficiency", "cleanup"},
            f"{path}.spec",
        )
        target = PluginSpec.parse(spec.get("target"), f"{path}.spec.target")
        support_raw = spec.get("supportPlugins") or []
        if not isinstance(support_raw, list):
            raise ContractError(f"{path}.spec.supportPlugins must be a list")
        support = tuple(PluginSpec.parse(item, f"{path}.spec.supportPlugins[{idx}]") for idx, item in enumerate(support_raw))

        agent = _mapping(spec.get("agent"), f"{path}.spec.agent")
        _only(agent, {"prompt", "timeoutSeconds"}, f"{path}.spec.agent")
        prompt = agent.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ContractError(f"{path}.spec.agent.prompt must be a non-empty path")
        timeout = agent.get("timeoutSeconds", 1800)
        if not isinstance(timeout, int) or timeout < 1:
            raise ContractError(f"{path}.spec.agent.timeoutSeconds must be a positive integer")

        environment = _mapping(spec.get("environment") or {}, f"{path}.spec.environment")
        _only(environment, {"components", "lock"}, f"{path}.spec.environment")
        _strings(environment.get("components"), f"{path}.spec.environment.components")
        if "lock" in environment and (not isinstance(environment["lock"], str) or not environment["lock"]):
            raise ContractError(f"{path}.spec.environment.lock must be a non-empty path")

        workspace = _mapping(spec.get("workspace") or {}, f"{path}.spec.workspace")
        _only(workspace, {"seed", "outputs"}, f"{path}.spec.workspace")
        seed = workspace.get("seed")
        if seed is not None and (not isinstance(seed, str) or not seed):
            raise ContractError(f"{path}.spec.workspace.seed must be a non-empty path")
        outputs = tuple(_strings(workspace.get("outputs"), f"{path}.spec.workspace.outputs"))
        if not outputs:
            raise ContractError(f"{path}.spec.workspace.outputs must declare at least one generated artifact")
        for pattern in outputs:
            candidate = Path(pattern)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ContractError(f"{path}: unsafe output pattern {pattern!r}")

        permissions = _mapping(spec.get("permissions") or {}, f"{path}.spec.permissions")
        _only(permissions, {"bashAllow"}, f"{path}.spec.permissions")
        bash_allow = tuple(_strings(permissions.get("bashAllow"), f"{path}.spec.permissions.bashAllow"))

        raw_oracles = spec.get("oracles") or []
        if not isinstance(raw_oracles, list) or not raw_oracles:
            raise ContractError(f"{path}.spec.oracles must be a non-empty list")
        oracles = tuple(OracleSpec.parse(item, f"{path}.spec.oracles[{idx}]") for idx, item in enumerate(raw_oracles))

        efficiency = _mapping(spec.get("efficiency") or {}, f"{path}.spec.efficiency")
        _only(
            efficiency,
            {"maxToolCalls", "maxLlmTurns", "maxTokens", "maxUnexpectedFailures", "forbiddenCommands", "expectedCommands"},
            f"{path}.spec.efficiency",
        )
        for key in ("maxToolCalls", "maxLlmTurns", "maxTokens", "maxUnexpectedFailures"):
            if key in efficiency and (not isinstance(efficiency[key], int) or efficiency[key] < 0):
                raise ContractError(f"{path}.spec.efficiency.{key} must be a non-negative integer")
        _strings(efficiency.get("forbiddenCommands"), f"{path}.spec.efficiency.forbiddenCommands")
        _strings(efficiency.get("expectedCommands"), f"{path}.spec.efficiency.expectedCommands")

        cleanup = _mapping(spec.get("cleanup") or {}, f"{path}.spec.cleanup")
        _only(cleanup, {"deleteNamespace", "deleteBucketPrefix"}, f"{path}.spec.cleanup")
        for key in ("deleteNamespace", "deleteBucketPrefix"):
            if key in cleanup and not isinstance(cleanup[key], bool):
                raise ContractError(f"{path}.spec.cleanup.{key} must be a boolean")

        root = path.parent
        if not (root / prompt).is_file():
            raise ContractError(f"{path}: prompt file does not exist: {prompt}")
        if seed and not (root / seed).exists():
            raise ContractError(f"{path}: seed path does not exist: {seed}")
        if environment.get("lock") and not (root / environment["lock"]).is_file():
            raise ContractError(f"{path}: environment lock does not exist: {environment['lock']}")

        return cls(
            path=path,
            name=name,
            description=description,
            target=target,
            support_plugins=support,
            prompt_file=prompt,
            timeout_seconds=timeout,
            environment=environment,
            seed=seed,
            outputs=outputs,
            bash_allow=bash_allow,
            oracles=oracles,
            efficiency=efficiency,
            cleanup=cleanup,
            tags=tuple(_strings(metadata.get("tags"), f"{path}.metadata.tags")),
        )


@dataclass(frozen=True)
class RunConfig:
    agent_repo: str
    agent_ref: str
    base_config: Path
    plugin_root: Path
    model_target: str | dict[str, str] | None = None
    repeats: int = 1
    keep_suite: bool = False
    artifacts_root: Path = Path(".datus-e2e/runs")
    cache_root: Path = Path(".datus-e2e/cache")

    @classmethod
    def parse(cls, raw: Any, base: Path) -> "RunConfig":
        data = _mapping(raw, "run config")
        _only(data, {"agent", "pluginRoot", "modelTarget", "repeats", "keepSuite", "artifactsRoot", "cacheRoot"}, "run config")
        agent = _mapping(data.get("agent"), "run config.agent")
        _only(agent, {"repo", "ref", "config"}, "run config.agent")
        for key in ("repo", "ref", "config"):
            if not isinstance(agent.get(key), str) or not agent[key]:
                raise ContractError(f"run config.agent.{key} must be a non-empty string")
        plugin_root = data.get("pluginRoot", ".")
        if not isinstance(plugin_root, str) or not plugin_root:
            raise ContractError("run config.pluginRoot must be a non-empty path")
        repeats = data.get("repeats", 1)
        if not isinstance(repeats, int) or repeats < 1 or repeats > 20:
            raise ContractError("run config.repeats must be between 1 and 20")
        target = data.get("modelTarget")
        if target is not None and not isinstance(target, (str, dict)):
            raise ContractError("run config.modelTarget must be a string or mapping")

        def resolve(value: str) -> Path:
            path = Path(value).expanduser()
            return (base / path).resolve() if not path.is_absolute() else path.resolve()

        base_config = resolve(agent["config"])
        if not base_config.is_file():
            raise ContractError(f"agent config does not exist: {base_config}")
        return cls(
            agent_repo=agent["repo"],
            agent_ref=agent["ref"],
            base_config=base_config,
            plugin_root=resolve(plugin_root),
            model_target=target,
            repeats=repeats,
            keep_suite=bool(data.get("keepSuite", False)),
            artifacts_root=resolve(data.get("artifactsRoot", ".datus-e2e/runs")),
            cache_root=resolve(data.get("cacheRoot", ".datus-e2e/cache")),
        )


def load_workflow(path: Path | str) -> Workflow:
    resolved = Path(path).resolve()
    with resolved.open(encoding="utf-8") as handle:
        return Workflow.parse(yaml.safe_load(handle), resolved)


def load_run_config(path: Path | str) -> RunConfig:
    resolved = Path(path).resolve()
    with resolved.open(encoding="utf-8") as handle:
        return RunConfig.parse(yaml.safe_load(handle), resolved.parent)
