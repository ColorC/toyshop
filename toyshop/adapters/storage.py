"""Storage Adapter — wraps toyshop.storage.database into StoragePort."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from toyshop.storage import database
from toyshop.ports.storage import StoragePort


class SQLiteStorageAdapter:
    """Wraps existing database module into StoragePort interface."""

    def init(self, db_path: Path) -> None:
        return database.init_database(db_path)

    def close(self) -> None:
        database.close_database()

    def create_project(self, name: str, root_path: str, **kwargs: Any) -> dict[str, Any]:
        return database.create_project(name, root_path, **kwargs)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return database.get_project(project_id)

    def find_project_by_path(self, path: str) -> dict[str, Any] | None:
        return database.find_project_by_path(path)

    def save_architecture(
        self,
        project_id: str,
        modules: list,
        interfaces: list,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return database.save_architecture_from_design(project_id, modules, interfaces, **kwargs)

    def create_workflow_run(
        self,
        project_id: str,
        workflow_type: str,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        return database.create_workflow_run(project_id, workflow_type, batch_id)

    def complete_workflow_run(
        self,
        run_id: str,
        status: str,
        result: dict | None = None,
    ) -> None:
        database.complete_workflow_run(run_id, status, result)
