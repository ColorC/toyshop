"""Wiki Adapter — wraps toyshop.storage.wiki into WikiPort."""

from __future__ import annotations

from typing import Any

from toyshop.storage import wiki
from toyshop.ports.wiki import WikiPort


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

    def list_versions(self, project_id: str) -> list[Any]:
        return wiki.list_versions(project_id)

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
