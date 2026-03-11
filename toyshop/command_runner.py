"""Unified subprocess command runner utilities."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass
class CommandRunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: Mapping[str, str] | None = None,
) -> CommandRunResult:
    """Execute command and normalize subprocess error handling."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandRunResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except subprocess.TimeoutExpired:
        return CommandRunResult(
            returncode=-1,
            stdout="",
            stderr=f"{' '.join(cmd)} timed out after {timeout}s",
            timed_out=True,
            error="timeout",
        )
    except Exception as e:
        return CommandRunResult(
            returncode=-1,
            stdout="",
            stderr=f"{' '.join(cmd)} execution error: {e}",
            error=str(e),
        )
