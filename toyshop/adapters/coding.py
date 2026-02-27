"""Coding Adapter — wraps toyshop.tdd_pipeline into CodingAgentPort."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from toyshop.tdd_pipeline import run_tdd_pipeline
from toyshop.ports.coding import CodingAgentPort


class OpenHandsCodingAdapter:
    """Wraps the existing TDD pipeline into CodingAgentPort interface."""

    def __init__(self, llm):
        self._llm = llm

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
        return run_tdd_pipeline(
            workspace=workspace,
            llm=self._llm,
            language=language,
            mode=mode,
            change_request=change_request,
        )

    def run_single_task(
        self,
        workspace: Path,
        task_description: str,
        allowed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        # Simplified: just run TDD with the minimal setup
        result = run_tdd_pipeline(
            workspace=workspace,
            llm=self._llm,
            mode="create",
        )
        return {
            "success": result.success,
            "summary": result.summary,
        }
