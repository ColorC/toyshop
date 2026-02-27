"""Wiki Port — abstracts versioned wiki operations."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WikiPort(Protocol):
    """Port for versioned wiki operations."""

    def create_version(
        self,
        project_id: str,
        snapshot_id: str | None,
        change_type: str,
        change_summary: str,
        **kwargs: Any,
    ) -> Any:
        """Create a new wiki version.

        Returns:
            WikiVersion-like object.
        """
        ...

    def get_latest_version(self, project_id: str) -> Any | None:
        """Get the most recent version for a project."""
        ...

    def get_version(self, version_id: str) -> Any | None:
        """Get a specific version by ID."""
        ...

    def list_versions(self, project_id: str, limit: int = 20) -> list[Any]:
        """List version history for a project."""
        ...

    def rollback_to_version(
        self,
        project_id: str,
        target_version: int,
        reason: str,
    ) -> Any:
        """Rollback to a previous version.

        Creates a new version pointing to old snapshot.
        """
        ...

    def diff_versions(
        self,
        project_id: str,
        from_version: int,
        to_version: int,
    ) -> Any:
        """Compare two versions."""
        ...

    def bind_git_commit(
        self,
        version_id: str,
        git_commit_hash: str,
    ) -> None:
        """Bind a git commit hash to a version."""
        ...

    def save_test_suite(
        self,
        version_id: str,
        test_files: list[str],
        test_cases: list[dict[str, str]],
        total_tests: int,
        passed: int,
        failed: int,
    ) -> Any:
        """Save test suite state for a version."""
        ...

    def extract_test_metadata(
        self,
        workspace: Any,
        language: str = "python",
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Extract test files and test cases from workspace."""
        ...

    def log_event(
        self,
        project_id: str,
        event_type: str,
        event_detail: str,
        version_id: str | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> None:
        """Append wiki changelog event."""
        ...
