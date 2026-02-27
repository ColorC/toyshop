"""Coding Agent Port — abstracts code generation agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CodingAgentPort(Protocol):
    """Port for code generation agents (OpenHands, future alternatives)."""

    def run_tdd(
        self,
        workspace: Path,
        design_md: str,
        spec_md: str,
        *,
        mode: str = "create",
        language: str = "python",
        change_request: str | None = None,
    ) -> Any:
        """Run TDD pipeline: generate tests, implement code, verify.

        Args:
            workspace: Project root directory
            design_md: Design document content
            spec_md: Spec document content
            mode: "create" for new code, "modify" for brownfield
            language: Target language
            change_request: Description of changes (for modify mode)

        Returns:
            TDDResult-like object with success, files_created, test_files, etc.
        """
        ...

    def run_single_task(
        self,
        workspace: Path,
        task_description: str,
        allowed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a single coding task with optional path restrictions.

        Returns:
            Dict with success, summary, changed_files keys.
        """
        ...
