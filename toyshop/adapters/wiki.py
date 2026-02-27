"""Wiki Adapter — wraps toyshop.storage.wiki into WikiPort."""

from __future__ import annotations

from typing import Any

from toyshop.storage import wiki


class SQLiteWikiAdapter:
    """Wraps existing wiki module into WikiPort interface."""

    def create_version(
        self,
        project_id: str,
        snapshot_id: str | None,
        change_type: str,
        change_summary: str,
        **kwargs: Any,
    ) -> Any:
        return wiki.create_version(
            project_id=project_id,
            snapshot_id=snapshot_id,
            change_type=change_type,
            change_summary=change_summary,
            **kwargs,
        )

    def get_latest_version(self, project_id: str) -> Any | None:
        return wiki.get_latest_version(project_id)

    def get_version(self, version_id: str) -> Any | None:
        return wiki.get_version(version_id)

    def list_versions(self, project_id: str, limit: int = 20) -> list[Any]:
        return wiki.list_versions(project_id, limit=limit)

    def rollback_to_version(
        self,
        project_id: str,
        target_version: int,
        reason: str,
    ) -> Any:
        return wiki.rollback_to_version(project_id, target_version, reason)

    def diff_versions(
        self,
        project_id: str,
        from_version: int,
        to_version: int,
    ) -> Any:
        return wiki.diff_versions(project_id, from_version, to_version)

    def bind_git_commit(self, version_id: str, git_commit_hash: str) -> None:
        wiki.bind_git_commit(version_id, git_commit_hash)

    def save_test_suite(
        self,
        version_id: str,
        test_files: list[str],
        test_cases: list[dict[str, str]],
        total_tests: int,
        passed: int,
        failed: int,
    ) -> Any:
        return wiki.save_test_suite(
            version_id=version_id,
            test_files=test_files,
            test_cases=test_cases,
            total_tests=total_tests,
            passed=passed,
            failed=failed,
        )

    def extract_test_metadata(
        self,
        workspace: Any,
        language: str = "python",
    ) -> tuple[list[str], list[dict[str, str]]]:
        return wiki.extract_test_metadata(workspace, language)

    def log_event(
        self,
        project_id: str,
        event_type: str,
        event_detail: str,
        version_id: str | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> None:
        wiki.log_event(project_id, event_type, event_detail, version_id, event_data)
