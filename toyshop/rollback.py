"""Rollback system for TDD debug pipeline.

Two layers:
- Probe rollback: ProbeInstrumentor restores files from in-memory backups
- Code rollback: git checkpoint/reset for reverting Coding Agent changes
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class RollbackManager:
    """Manages git-based checkpoints for code rollback."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._ensure_git_init()

    def _ensure_git_init(self) -> None:
        """Initialize git repo if not already initialized."""
        git_dir = self.workspace / ".git"
        if not git_dir.exists():
            self._run_git("init")
            self._run_git("config", "user.email", "tdd-pipeline@toyshop.local")
            self._run_git("config", "user.name", "TDD Pipeline")
            # Initial commit so we have a base
            self._run_git("add", "-A")
            self._run_git("commit", "--allow-empty", "-m", "checkpoint: init")
        else:
            # Ensure config exists even if repo was already initialized
            email = self._run_git("config", "--get", "user.email")
            if not email:
                self._run_git("config", "user.email", "tdd-pipeline@toyshop.local")
                self._run_git("config", "user.name", "TDD Pipeline")

    def _run_git(self, *args: str) -> str:
        """Run a git command in the workspace directory."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            # Non-fatal: some git commands return non-zero for benign reasons
            pass
        return result.stdout.strip()

    def checkpoint(self, label: str) -> str:
        """Create a git commit as a checkpoint. Returns commit hash."""
        self._run_git("add", "-A")
        self._run_git("commit", "--allow-empty", "-m", f"checkpoint: {label}")
        commit_hash = self._run_git("rev-parse", "HEAD")
        return commit_hash

    def rollback_to(self, commit_hash: str) -> None:
        """Hard reset to a checkpoint."""
        self._run_git("reset", "--hard", commit_hash)

    def diff_since(self, commit_hash: str) -> str:
        """Show changes since a checkpoint."""
        return self._run_git("diff", commit_hash, "HEAD")

    def is_clean(self) -> bool:
        """Check if working directory is clean."""
        status = self._run_git("status", "--porcelain")
        return len(status.strip()) == 0
