"""Logged subprocess execution used by the trusted harness."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandFailed(RuntimeError):
    def __init__(self, result: CommandResult):
        self.result = result
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        super().__init__(f"command failed ({result.returncode}): {' '.join(result.argv)}: {tail[0]}")


def run_command(
    argv: Sequence[str | Path],
    *,
    cwd: Path,
    log_dir: Path,
    name: str,
    env: Mapping[str, str] | None = None,
    timeout: int = 600,
    check: bool = True,
) -> CommandResult:
    args = tuple(str(item) for item in argv)
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(key): str(value) for key, value in env.items()})
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        result = CommandResult(args, completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(args, 124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")
    (log_dir / f"{name}.stdout.log").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{name}.stderr.log").write_text(result.stderr, encoding="utf-8")
    if check and result.returncode:
        raise CommandFailed(result)
    return result
