"""Storage Port — abstracts project/architecture persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StoragePort(Protocol):
    """Port for project/architecture persistence."""

    def init(self, db_path: Path) -> None:
        """Initialize database at given path."""
        ...

    def close(self) -> None:
        """Close database connection."""
        ...

    def create_project(
        self,
        name: str,
        root_path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a new project record.

        Returns:
            Project dict with id, name, root_path, etc.
        """
        ...

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """Get project by ID."""
        ...

    def find_project_by_path(self, path: str) -> dict[str, Any] | None:
        """Find project by root path."""
        ...

    def save_architecture(
        self,
        project_id: str,
        modules: list[dict[str, Any]],
        interfaces: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Save architecture snapshot.

        Returns:
            Snapshot dict with id.
        """
        ...

    def create_workflow_run(
        self,
        project_id: str,
        workflow_type: str,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a workflow run record."""
        ...

    def complete_workflow_run(
        self,
        run_id: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Mark workflow run as completed or failed."""
        ...

    def append_process_step(
        self,
        run_id: str,
        seq: int,
        stage: str,
        action: str,
        status: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Append a process step in execution trace."""
        ...

    def save_code_diff(
        self,
        run_id: str,
        step_id: str,
        file_path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Save code diff metadata bound to a process step."""
        ...

    def save_gate_result(
        self,
        run_id: str,
        step_id: str,
        gate_type: str,
        passed: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Save gate result bound to a process step."""
        ...
