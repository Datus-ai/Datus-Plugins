"""Install a pinned Datus agent, pack plugins, and run print mode."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .schema import PluginSpec, RunConfig, Workflow
from .subprocesses import run_command


SHA = re.compile(r"^[0-9a-f]{40}$")


def _replace(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, variables) for item in value]
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace("{{" + key + "}}", replacement)
        return value
    return value


@dataclass(frozen=True)
class AgentRuntime:
    sha: str
    venv: Path
    datus: Path
    python: Path
    config: Path
    home: Path
    workspace: Path
    bundles: tuple[Path, ...]


def resolve_agent_sha(repo: str, ref: str, *, repo_root: Path, log_dir: Path) -> str:
    local = Path(repo).expanduser()
    if local.exists():
        result = run_command(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=local.resolve(),
            log_dir=log_dir,
            name="agent-resolve-local",
        )
        sha = result.stdout.strip()
    elif SHA.fullmatch(ref):
        sha = ref
    else:
        result = run_command(
            ["git", "ls-remote", repo, ref, f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"],
            cwd=repo_root,
            log_dir=log_dir,
            name="agent-resolve-remote",
        )
        rows = [line.split(maxsplit=1) for line in result.stdout.splitlines() if line.strip()]
        branch_ref = ref if ref.startswith("refs/heads/") else f"refs/heads/{ref}"
        tag_ref = ref if ref.startswith("refs/tags/") else f"refs/tags/{ref}"
        by_ref = {row[1]: row[0] for row in rows if len(row) == 2}
        candidates: list[str] = []
        if branch_ref in by_ref:
            candidates.append(by_ref[branch_ref])
        if f"{tag_ref}^{{}}" in by_ref:
            candidates.append(by_ref[f"{tag_ref}^{{}}"])
        elif tag_ref in by_ref:
            candidates.append(by_ref[tag_ref])
        if ref in by_ref and ref not in {branch_ref, tag_ref}:
            candidates.append(by_ref[ref])
        unique = list(dict.fromkeys(candidates))
        if len(unique) != 1:
            raise ValueError(f"agent ref {ref!r} did not resolve to exactly one commit")
        sha = unique[0]
    if not SHA.fullmatch(sha):
        raise ValueError(f"resolved agent revision is not a full SHA: {sha!r}")
    return sha


def agent_install_source(repo: str, sha: str) -> str:
    """Return a PEP 508 VCS source that always installs the resolved commit."""
    local = Path(repo).expanduser()
    location = local.resolve().as_uri() if local.exists() else repo
    if location.startswith("git+"):
        location = location.removeprefix("git+")
    return f"git+{location}@{sha}"


def prepare_agent_venv(config: RunConfig, sha: str, *, repo_root: Path, log_dir: Path) -> tuple[Path, Path, Path]:
    venv = config.cache_root / "agents" / sha / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    datus = venv / ("Scripts/datus.exe" if os.name == "nt" else "bin/datus")
    if datus.is_file():
        return venv, python, datus
    venv.parent.mkdir(parents=True, exist_ok=True)
    run_command(["uv", "venv", str(venv), "--python", "3.12"], cwd=repo_root, log_dir=log_dir, name="agent-venv", timeout=300)
    source = agent_install_source(config.agent_repo, sha)
    run_command(["uv", "pip", "install", "--python", str(python), source], cwd=repo_root, log_dir=log_dir, name="agent-install", timeout=1800)
    if not datus.is_file():
        raise RuntimeError("installed agent did not provide the datus executable")
    return venv, python, datus


def _write_configs(
    workflow: Workflow,
    run_config: RunConfig,
    variables: dict[str, str],
    run_dir: Path,
    workspace: Path,
) -> tuple[Path, Path, Path]:
    data = yaml.safe_load(run_config.base_config.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("base agent config must be a mapping")
    agent = data.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise ValueError("base agent config agent key must be a mapping")
    home = run_dir / "datus-home"
    home.mkdir(parents=True, exist_ok=True)
    agent["home"] = str(home)
    agent["project_name"] = variables["RUN_ID"].replace("-", "_")
    services = agent.setdefault("services", {})
    if not isinstance(services, dict):
        raise ValueError("base agent config agent.services key must be a mapping")
    datasources = services.setdefault("datasources", {})
    if not isinstance(datasources, dict):
        raise ValueError("base agent config agent.services.datasources key must be a mapping")
    database = workspace / "lab.sqlite"
    database.touch()
    datasources["lab"] = {"type": "sqlite", "uri": f"sqlite:///{database}"}
    plugins = agent.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise ValueError("base agent config agent.plugins key must be a mapping")
    for plugin in (workflow.target, *workflow.support_plugins):
        plugins[plugin.name] = {"e2e": {"default": True, **_replace(plugin.profile, variables)}}
    for key in ("permissions", "filesystem", "bash"):
        if not isinstance(agent.setdefault(key, {}), dict):
            raise ValueError(f"base agent config agent.{key} key must be a mapping")
    agent["permissions"]["profile"] = "auto"
    agent["filesystem"]["strict"] = True
    sandbox = agent["bash"].setdefault("sandbox", {})
    if not isinstance(sandbox, dict):
        raise ValueError("base agent config agent.bash.sandbox key must be a mapping")
    sandbox.update({"enabled": True, "mode": "strict", "deny_network": False})

    # Child `datus <plugin>` commands launched by the bash tool resolve
    # `./conf/agent.yml`; placing the same run-scoped config there avoids HOME
    # overrides and prevents them from silently loading the user's global store.
    config_dir = workspace / "conf"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "agent.yml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if os.name != "nt":
        config_path.chmod(0o400)
        config_dir.chmod(0o500)

    project = {
        "project_name": variables["RUN_ID"],
        "default_datasource": "lab",
        "plugins": {plugin.name: "e2e" for plugin in (workflow.target, *workflow.support_plugins)},
        "bash_allow": list(workflow.bash_allow),
        "sandbox": "strict",
    }
    if run_config.model_target is not None:
        project["target"] = run_config.model_target
    project_dir = workspace / ".datus"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_path = project_dir / "config.yml"
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    return config_path, project_path, home


def _pack_and_install(
    plugins: tuple[PluginSpec, ...],
    run_config: RunConfig,
    datus: Path,
    python: Path,
    agent_config: Path,
    run_dir: Path,
    repo_root: Path,
) -> tuple[Path, ...]:
    bundles_dir = run_dir / "bundles"
    logs = run_dir / "logs"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    bundles: list[Path] = []
    for index, plugin in enumerate(plugins):
        source = (run_config.plugin_root / plugin.path).resolve()
        if not source.is_dir() or run_config.plugin_root not in source.parents:
            raise ValueError(f"plugin path escapes plugin root or does not exist: {source}")
        before = set(bundles_dir.glob("*.zip"))
        run_command(
            [datus, "plugin", "pack", source, "--with-deps", "-o", bundles_dir],
            cwd=repo_root,
            log_dir=logs,
            name=f"pack-{index}-{plugin.name}",
            timeout=1800,
        )
        created = sorted(set(bundles_dir.glob("*.zip")) - before, key=lambda item: item.stat().st_mtime)
        if not created:
            candidates = sorted(bundles_dir.glob(f"{plugin.distribution.replace('-', '_')}*.zip"), key=lambda item: item.stat().st_mtime)
            created = candidates[-1:]
        if len(created) != 1:
            raise RuntimeError(f"pack did not produce one identifiable bundle for {plugin.name}")
        bundle = created[0]
        run_command(
            [python, repo_root / "tests/e2e/harness/install_plugin.py", "--config", agent_config, "--bundle", bundle],
            cwd=repo_root,
            log_dir=logs,
            name=f"install-{index}-{plugin.name}",
            timeout=1800,
        )
        bundles.append(bundle)
    return tuple(bundles)


def prepare_runtime(
    workflow: Workflow,
    run_config: RunConfig,
    variables: dict[str, str],
    run_dir: Path,
    workspace: Path,
    repo_root: Path,
) -> AgentRuntime:
    logs = run_dir / "logs"
    sha = resolve_agent_sha(run_config.agent_repo, run_config.agent_ref, repo_root=repo_root, log_dir=logs)
    venv, python, datus = prepare_agent_venv(run_config, sha, repo_root=repo_root, log_dir=logs)
    agent_config, _, home = _write_configs(workflow, run_config, variables, run_dir, workspace)
    bundles = _pack_and_install((workflow.target, *workflow.support_plugins), run_config, datus, python, agent_config, run_dir, repo_root)
    return AgentRuntime(sha, venv, datus, python, agent_config, home, workspace, bundles)


def render_prompt(workflow: Workflow, variables: dict[str, str]) -> str:
    prompt = (workflow.path.parent / workflow.prompt_file).read_text(encoding="utf-8")
    for key, value in variables.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", prompt)))
    if unresolved:
        raise ValueError(f"prompt has unresolved variables: {unresolved}")
    return prompt


def run_datus(runtime: AgentRuntime, workflow: Workflow, prompt: str, run_dir: Path):
    result = run_command(
        [
            runtime.datus,
            "--config", runtime.config,
            "--datasource", "lab",
            "--session-scope", run_dir.name,
            "--filesystem-strict",
            "--permission-mode", "auto",
            "--execution-mode", "workflow",
            "-p", prompt,
        ],
        cwd=runtime.workspace,
        log_dir=run_dir,
        name="datus",
        timeout=workflow.timeout_seconds,
        check=False,
    )
    (run_dir / "stdout.jsonl").write_text(result.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(result.stderr, encoding="utf-8")
    return result
