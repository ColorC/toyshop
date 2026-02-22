"""SQLite storage for architecture snapshots.

Simple persistence layer using Python's built-in sqlite3.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator


# Global connection
_db: sqlite3.Connection | None = None


def init_database(db_path: str | Path) -> None:
    """Initialize the database connection and create tables."""
    global _db
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _db = sqlite3.connect(str(path), check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _create_tables()


def close_database() -> None:
    """Close the database connection."""
    global _db
    if _db:
        _db.close()
        _db = None


def get_db() -> sqlite3.Connection:
    """Get the database connection."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db


@contextmanager
def transaction() -> Generator[sqlite3.Cursor, None, None]:
    """Context manager for database transactions."""
    db = get_db()
    cursor = db.cursor()
    try:
        yield cursor
        db.commit()
    except Exception:
        db.rollback()
        raise


def _create_tables() -> None:
    """Create database tables if they don't exist."""
    with transaction() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                root_path TEXT NOT NULL,
                current_version TEXT DEFAULT '1.0.0',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS modules (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                responsibilities TEXT,
                dependencies TEXT,
                file_path TEXT,
                symid TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS interfaces (
                id TEXT PRIMARY KEY,
                module_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                signature TEXT,
                description TEXT,
                symid TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (module_id) REFERENCES modules(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version TEXT NOT NULL,
                modules_json TEXT,
                interfaces_json TEXT,
                dependencies_json TEXT,
                source TEXT DEFAULT 'generated',
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)


# ---------------------------------------------------------------------------
# Project operations
# ---------------------------------------------------------------------------


def create_project(name: str, root_path: str) -> dict[str, Any]:
    """Create a new project record."""
    import uuid
    now = datetime.utcnow().isoformat()
    project_id = str(uuid.uuid4())[:8]

    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO projects (id, name, root_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, name, root_path, now, now),
        )

    return {
        "id": project_id,
        "name": name,
        "root_path": root_path,
        "current_version": "1.0.0",
        "created_at": now,
        "updated_at": now,
    }


def get_project(project_id: str) -> dict[str, Any] | None:
    """Get a project by ID."""
    db = get_db()
    cur = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Architecture operations
# ---------------------------------------------------------------------------


def save_architecture_from_design(
    project_id: str,
    modules: list[dict[str, Any]],
    interfaces: list[dict[str, Any]],
    source: str = "generated",
) -> dict[str, Any]:
    """Save architecture snapshot from design."""
    import uuid
    now = datetime.utcnow().isoformat()
    snapshot_id = str(uuid.uuid4())[:8]

    # Get current version
    project = get_project(project_id)
    version = project.get("current_version", "1.0.0") if project else "1.0.0"

    with transaction() as cur:
        # Save modules
        for mod in modules:
            mod_id = mod.get("id", str(uuid.uuid4())[:8])
            cur.execute(
                """
                INSERT OR REPLACE INTO modules
                (id, project_id, name, description, responsibilities, dependencies, file_path, symid, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mod_id,
                    project_id,
                    mod.get("name", ""),
                    mod.get("description", ""),
                    json.dumps(mod.get("responsibilities", [])),
                    json.dumps(mod.get("dependencies", [])),
                    mod.get("filePath", mod.get("file_path", "")),
                    mod.get("symid", mod_id),
                    now,
                    now,
                ),
            )

        # Save interfaces
        for intf in interfaces:
            intf_id = intf.get("id", str(uuid.uuid4())[:8])
            cur.execute(
                """
                INSERT OR REPLACE INTO interfaces
                (id, module_id, name, type, signature, description, symid, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intf_id,
                    intf.get("moduleId", intf.get("module_id", "")),
                    intf.get("name", ""),
                    intf.get("type", "function"),
                    intf.get("signature", ""),
                    intf.get("description", ""),
                    intf.get("symid", intf_id),
                    now,
                    now,
                ),
            )

        # Save snapshot
        cur.execute(
            """
            INSERT INTO snapshots
            (id, project_id, version, modules_json, interfaces_json, dependencies_json, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                project_id,
                version,
                json.dumps(modules),
                json.dumps(interfaces),
                json.dumps([]),
                source,
                now,
            ),
        )

    return {
        "id": snapshot_id,
        "project_id": project_id,
        "version": version,
        "created_at": now,
    }


def get_latest_snapshot(project_id: str) -> dict[str, Any] | None:
    """Get the latest architecture snapshot for a project."""
    db = get_db()
    cur = db.execute(
        """
        SELECT * FROM snapshots
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id,),
    )
    row = cur.fetchone()
    if row:
        result = dict(row)
        result["modules"] = json.loads(result.get("modules_json", "[]"))
        result["interfaces"] = json.loads(result.get("interfaces_json", "[]"))
        return result
    return None
