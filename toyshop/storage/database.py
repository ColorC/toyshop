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
                project_type TEXT DEFAULT 'python',
                language TEXT DEFAULT 'python',
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

        # --- Wiki versioning tables ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wiki_versions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                git_commit_hash TEXT,
                snapshot_id TEXT,
                parent_version_id TEXT,
                change_type TEXT NOT NULL DEFAULT 'create',
                change_summary TEXT NOT NULL DEFAULT '',
                change_source TEXT NOT NULL DEFAULT 'tdd',
                batch_id TEXT,
                pipeline_result_json TEXT,
                proposal_md TEXT,
                design_md TEXT,
                tasks_md TEXT,
                spec_md TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_wv_proj_num
            ON wiki_versions(project_id, version_number)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_wv_git
            ON wiki_versions(git_commit_hash)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wiki_test_suites (
                id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                test_files_json TEXT NOT NULL,
                test_cases_json TEXT NOT NULL,
                total_tests INTEGER NOT NULL DEFAULT 0,
                passed INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (version_id) REFERENCES wiki_versions(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wiki_changelog (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version_id TEXT,
                event_type TEXT NOT NULL,
                event_detail TEXT NOT NULL DEFAULT '',
                event_data_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        # --- Project norms ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS project_norms (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                norm_type TEXT NOT NULL,
                norm_name TEXT NOT NULL,
                norm_description TEXT NOT NULL DEFAULT '',
                norm_rules_json TEXT NOT NULL DEFAULT '[]',
                severity TEXT NOT NULL DEFAULT 'warning',
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        # --- Architecture health history ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS architecture_health_history (
                id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                warning_count INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (version_id) REFERENCES wiki_versions(id),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        # --- Run events ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_events (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                batch_id TEXT,
                event_type TEXT NOT NULL,
                event_data_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        # --- Workflow runs (pipeline execution tracking) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                workflow_type TEXT NOT NULL,
                batch_id TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        # --- Change plans (impact analysis records) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS change_plans (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version_id TEXT,
                change_request TEXT NOT NULL,
                impact_json TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)


# ---------------------------------------------------------------------------
# Project operations
# ---------------------------------------------------------------------------


def create_project(name: str, root_path: str, project_type: str = "python", language: str = "python") -> dict[str, Any]:
    """Create a new project record."""
    import uuid
    now = datetime.utcnow().isoformat()
    project_id = str(uuid.uuid4())[:8]

    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO projects (id, name, root_path, project_type, language, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, name, root_path, project_type, language, now, now),
        )

    return {
        "id": project_id,
        "name": name,
        "root_path": root_path,
        "current_version": "1.0.0",
        "project_type": project_type,
        "language": language,
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


# ---------------------------------------------------------------------------
# Project query helpers
# ---------------------------------------------------------------------------


def list_projects() -> list[dict[str, Any]]:
    """List all projects."""
    db = get_db()
    cur = db.execute("SELECT * FROM projects ORDER BY created_at DESC")
    return [dict(row) for row in cur.fetchall()]


def find_project_by_path(root_path: str) -> dict[str, Any] | None:
    """Find a project by its root_path."""
    db = get_db()
    cur = db.execute("SELECT * FROM projects WHERE root_path = ?", (root_path,))
    row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Project norms
# ---------------------------------------------------------------------------


def save_project_norm(
    project_id: str,
    norm_type: str,
    norm_name: str,
    description: str = "",
    rules: list[str] | None = None,
    severity: str = "warning",
) -> dict[str, Any]:
    """Save a project norm."""
    import uuid
    now = datetime.utcnow().isoformat()
    norm_id = str(uuid.uuid4())[:8]

    with transaction() as cur:
        cur.execute(
            """INSERT INTO project_norms
            (id, project_id, norm_type, norm_name, norm_description,
             norm_rules_json, severity, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (norm_id, project_id, norm_type, norm_name, description,
             json.dumps(rules or []), severity, now),
        )

    return {
        "id": norm_id,
        "project_id": project_id,
        "norm_type": norm_type,
        "norm_name": norm_name,
        "norm_description": description,
        "norm_rules_json": json.dumps(rules or []),
        "severity": severity,
        "created_at": now,
    }


def get_project_norms(
    project_id: str,
    norm_type: str | None = None,
) -> list[dict[str, Any]]:
    """Get project norms, optionally filtered by type."""
    db = get_db()
    if norm_type:
        cur = db.execute(
            "SELECT * FROM project_norms WHERE project_id = ? AND norm_type = ? ORDER BY created_at",
            (project_id, norm_type),
        )
    else:
        cur = db.execute(
            "SELECT * FROM project_norms WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        )
    results = []
    for row in cur.fetchall():
        d = dict(row)
        d["rules"] = json.loads(d.get("norm_rules_json", "[]"))
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Architecture health history
# ---------------------------------------------------------------------------


def save_health_check(
    version_id: str,
    project_id: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Save architecture health check results for a version."""
    import uuid
    now = datetime.utcnow().isoformat()
    check_id = str(uuid.uuid4())[:8]

    with transaction() as cur:
        cur.execute(
            """INSERT INTO architecture_health_history
            (id, version_id, project_id, warnings_json, warning_count, checked_at)
            VALUES (?,?,?,?,?,?)""",
            (check_id, version_id, project_id, json.dumps(warnings),
             len(warnings), now),
        )

    return {
        "id": check_id,
        "version_id": version_id,
        "project_id": project_id,
        "warnings": warnings,
        "warning_count": len(warnings),
        "checked_at": now,
    }


def get_health_history(
    project_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Get architecture health check history for a project."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM architecture_health_history WHERE project_id = ? "
        "ORDER BY checked_at DESC LIMIT ?",
        (project_id, limit),
    )
    results = []
    for row in cur.fetchall():
        d = dict(row)
        d["warnings"] = json.loads(d.get("warnings_json", "[]"))
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Workflow runs
# ---------------------------------------------------------------------------


def create_workflow_run(
    project_id: str,
    workflow_type: str,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Create a new workflow run record (status=running)."""
    import uuid
    now = datetime.utcnow().isoformat()
    run_id = str(uuid.uuid4())[:8]

    with transaction() as cur:
        cur.execute(
            """INSERT INTO workflow_runs
            (id, project_id, workflow_type, batch_id, status, started_at, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (run_id, project_id, workflow_type, batch_id, "running", now, now),
        )

    return {
        "id": run_id,
        "project_id": project_id,
        "workflow_type": workflow_type,
        "batch_id": batch_id,
        "status": "running",
        "started_at": now,
    }


def complete_workflow_run(
    run_id: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    """Mark a workflow run as completed/failed."""
    now = datetime.utcnow().isoformat()
    with transaction() as cur:
        cur.execute(
            """UPDATE workflow_runs
            SET status = ?, completed_at = ?, result_json = ?
            WHERE id = ?""",
            (status, now, json.dumps(result) if result else None, run_id),
        )


def get_workflow_runs(
    project_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Get workflow run history for a project."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM workflow_runs WHERE project_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (project_id, limit),
    )
    results = []
    for row in cur.fetchall():
        d = dict(row)
        if d.get("result_json"):
            d["result"] = json.loads(d["result_json"])
        else:
            d["result"] = None
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Change plans
# ---------------------------------------------------------------------------


def create_change_plan(
    project_id: str,
    change_request: str,
    version_id: str | None = None,
    impact_json: str | None = None,
) -> dict[str, Any]:
    """Create a change plan record."""
    import uuid
    now = datetime.utcnow().isoformat()
    plan_id = str(uuid.uuid4())[:8]

    with transaction() as cur:
        cur.execute(
            """INSERT INTO change_plans
            (id, project_id, version_id, change_request, impact_json, status, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (plan_id, project_id, version_id, change_request, impact_json, "draft", now),
        )

    return {
        "id": plan_id,
        "project_id": project_id,
        "version_id": version_id,
        "change_request": change_request,
        "status": "draft",
        "created_at": now,
    }


def get_change_plans(
    project_id: str,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Get change plans for a project."""
    db = get_db()
    if status:
        cur = db.execute(
            "SELECT * FROM change_plans WHERE project_id = ? AND status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (project_id, status, limit),
        )
    else:
        cur = db.execute(
            "SELECT * FROM change_plans WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )
    results = []
    for row in cur.fetchall():
        d = dict(row)
        if d.get("impact_json"):
            d["impact"] = json.loads(d["impact_json"])
        else:
            d["impact"] = None
        results.append(d)
    return results
