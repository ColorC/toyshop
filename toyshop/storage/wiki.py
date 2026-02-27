"""Versioned Software Wiki — architecture version history with git binding.

Tracks wiki versions (architecture snapshots + frozen openspec docs),
test suite state per version, and an audit changelog.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from toyshop.storage.database import get_db, transaction, get_latest_snapshot


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WikiVersion:
    """A single version in the wiki history."""

    id: str
    project_id: str
    version_number: int
    git_commit_hash: str | None
    snapshot_id: str | None
    parent_version_id: str | None
    change_type: str  # create | modify | rollback
    change_summary: str
    change_source: str  # tdd | change_pipeline | manual
    batch_id: str | None
    created_at: str
    # Frozen openspec content (loaded on demand)
    proposal_md: str | None = None
    design_md: str | None = None
    tasks_md: str | None = None
    spec_md: str | None = None
    pipeline_result_json: str | None = None


@dataclass
class WikiTestSuite:
    """Test suite state at a specific version."""

    id: str
    version_id: str
    test_files: list[str]
    test_cases: list[dict[str, str]]
    total_tests: int
    passed: int
    failed: int
    created_at: str = ""


@dataclass
class VersionDiff:
    """Diff between two wiki versions."""

    from_version: int
    to_version: int
    modules_added: list[str]
    modules_removed: list[str]
    modules_modified: list[str]
    interfaces_added: list[str]
    interfaces_removed: list[str]
    interfaces_modified: list[str]
    tests_added: list[str]
    tests_removed: list[str]
    change_summary: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:8]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_version(row: Any) -> WikiVersion:
    d = dict(row)
    return WikiVersion(
        id=d["id"],
        project_id=d["project_id"],
        version_number=d["version_number"],
        git_commit_hash=d.get("git_commit_hash"),
        snapshot_id=d.get("snapshot_id"),
        parent_version_id=d.get("parent_version_id"),
        change_type=d["change_type"],
        change_summary=d["change_summary"],
        change_source=d["change_source"],
        batch_id=d.get("batch_id"),
        created_at=d["created_at"],
        proposal_md=d.get("proposal_md"),
        design_md=d.get("design_md"),
        tasks_md=d.get("tasks_md"),
        spec_md=d.get("spec_md"),
        pipeline_result_json=d.get("pipeline_result_json"),
    )

# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def create_version(
    project_id: str,
    snapshot_id: str | None,
    change_type: str,
    change_summary: str,
    change_source: str = "tdd",
    batch_id: str | None = None,
    pipeline_result_json: str | None = None,
    openspec_dir: Path | None = None,
) -> WikiVersion:
    """Create a new wiki version after a pipeline completes."""
    db = get_db()
    vid = _uid()
    now = _now()

    # Determine version_number and parent
    cur = db.execute(
        "SELECT id, version_number FROM wiki_versions "
        "WHERE project_id = ? ORDER BY version_number DESC LIMIT 1",
        (project_id,),
    )
    row = cur.fetchone()
    if row:
        version_number = row["version_number"] + 1
        parent_version_id = row["id"]
    else:
        version_number = 1
        parent_version_id = None

    # Freeze openspec content
    proposal_md = design_md = tasks_md = spec_md = None
    if openspec_dir and Path(openspec_dir).is_dir():
        odir = Path(openspec_dir)
        p = odir / "proposal.md"
        if p.exists():
            proposal_md = p.read_text(encoding="utf-8")
        p = odir / "design.md"
        if p.exists():
            design_md = p.read_text(encoding="utf-8")
        p = odir / "tasks.md"
        if p.exists():
            tasks_md = p.read_text(encoding="utf-8")
        p = odir / "spec.md"
        if p.exists():
            spec_md = p.read_text(encoding="utf-8")

    with transaction() as c:
        c.execute(
            """INSERT INTO wiki_versions
            (id, project_id, version_number, snapshot_id, parent_version_id,
             change_type, change_summary, change_source, batch_id,
             pipeline_result_json, proposal_md, design_md, tasks_md, spec_md,
             created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, project_id, version_number, snapshot_id, parent_version_id,
             change_type, change_summary, change_source, batch_id,
             pipeline_result_json, proposal_md, design_md, tasks_md, spec_md,
             now),
        )

    return WikiVersion(
        id=vid, project_id=project_id, version_number=version_number,
        git_commit_hash=None, snapshot_id=snapshot_id,
        parent_version_id=parent_version_id,
        change_type=change_type, change_summary=change_summary,
        change_source=change_source, batch_id=batch_id, created_at=now,
        proposal_md=proposal_md, design_md=design_md,
        tasks_md=tasks_md, spec_md=spec_md,
        pipeline_result_json=pipeline_result_json,
    )


def bind_git_commit(version_id: str, git_commit_hash: str) -> None:
    """Bind a git commit hash to an existing wiki version."""
    with transaction() as c:
        c.execute(
            "UPDATE wiki_versions SET git_commit_hash = ? WHERE id = ?",
            (git_commit_hash, version_id),
        )
    # Also log it
    db = get_db()
    cur = db.execute("SELECT project_id FROM wiki_versions WHERE id = ?", (version_id,))
    row = cur.fetchone()
    if row:
        log_event(row["project_id"], "git_bound",
                  f"Bound commit {git_commit_hash[:8]} to version",
                  version_id=version_id,
                  event_data={"git_commit_hash": git_commit_hash})

def save_test_suite(
    version_id: str,
    test_files: list[str],
    test_cases: list[dict[str, str]],
    total_tests: int,
    passed: int,
    failed: int,
) -> WikiTestSuite:
    """Save test suite state for a version."""
    sid = _uid()
    now = _now()
    with transaction() as c:
        c.execute(
            """INSERT INTO wiki_test_suites
            (id, version_id, test_files_json, test_cases_json,
             total_tests, passed, failed, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (sid, version_id, json.dumps(test_files),
             json.dumps(test_cases), total_tests, passed, failed, now),
        )
    return WikiTestSuite(
        id=sid, version_id=version_id, test_files=test_files,
        test_cases=test_cases, total_tests=total_tests,
        passed=passed, failed=failed, created_at=now,
    )


# ---------------------------------------------------------------------------
# Query operations
# ---------------------------------------------------------------------------


def get_version(version_id: str) -> WikiVersion | None:
    """Get a specific version by ID."""
    db = get_db()
    cur = db.execute("SELECT * FROM wiki_versions WHERE id = ?", (version_id,))
    row = cur.fetchone()
    return _row_to_version(row) if row else None


def get_version_by_commit(git_commit_hash: str) -> WikiVersion | None:
    """Get the wiki version associated with a git commit."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM wiki_versions WHERE git_commit_hash = ?",
        (git_commit_hash,),
    )
    row = cur.fetchone()
    return _row_to_version(row) if row else None


def get_latest_version(project_id: str) -> WikiVersion | None:
    """Get the most recent version for a project."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM wiki_versions WHERE project_id = ? "
        "ORDER BY version_number DESC LIMIT 1",
        (project_id,),
    )
    row = cur.fetchone()
    return _row_to_version(row) if row else None


def get_version_by_number(
    project_id: str, version_number: int
) -> WikiVersion | None:
    """Get a version by its sequential number within a project."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM wiki_versions "
        "WHERE project_id = ? AND version_number = ?",
        (project_id, version_number),
    )
    row = cur.fetchone()
    return _row_to_version(row) if row else None


def list_versions(
    project_id: str, limit: int = 20, offset: int = 0
) -> list[WikiVersion]:
    """List version history for a project, newest first."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM wiki_versions WHERE project_id = ? "
        "ORDER BY version_number DESC LIMIT ? OFFSET ?",
        (project_id, limit, offset),
    )
    return [_row_to_version(row) for row in cur.fetchall()]


def get_test_suite(version_id: str) -> WikiTestSuite | None:
    """Get the test suite for a specific version."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM wiki_test_suites WHERE version_id = ?",
        (version_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    return WikiTestSuite(
        id=d["id"], version_id=d["version_id"],
        test_files=json.loads(d["test_files_json"]),
        test_cases=json.loads(d["test_cases_json"]),
        total_tests=d["total_tests"], passed=d["passed"],
        failed=d["failed"], created_at=d["created_at"],
    )

# ---------------------------------------------------------------------------
# Diff / comparison
# ---------------------------------------------------------------------------


def diff_versions(
    project_id: str,
    from_version_number: int,
    to_version_number: int,
) -> VersionDiff:
    """Compare two versions and return structured diff."""
    v_from = get_version_by_number(project_id, from_version_number)
    v_to = get_version_by_number(project_id, to_version_number)
    if not v_from or not v_to:
        raise ValueError(
            f"Version not found: from={from_version_number}, to={to_version_number}"
        )

    # Load snapshot data for module/interface comparison
    snap_from = _load_snapshot_modules_interfaces(v_from.snapshot_id)
    snap_to = _load_snapshot_modules_interfaces(v_to.snapshot_id)

    mods_from = {m["name"] for m in snap_from["modules"]}
    mods_to = {m["name"] for m in snap_to["modules"]}

    intfs_from = {i["name"] for i in snap_from["interfaces"]}
    intfs_to = {i["name"] for i in snap_to["interfaces"]}

    # Detect modified: same name but different signature
    sig_from = {i["name"]: i.get("signature", "") for i in snap_from["interfaces"]}
    sig_to = {i["name"]: i.get("signature", "") for i in snap_to["interfaces"]}
    common_intfs = intfs_from & intfs_to
    modified_intfs = [n for n in common_intfs if sig_from[n] != sig_to[n]]

    # Module modification: same name but different responsibilities/deps
    resp_from = {m["name"]: m.get("responsibilities", "") for m in snap_from["modules"]}
    resp_to = {m["name"]: m.get("responsibilities", "") for m in snap_to["modules"]}
    common_mods = mods_from & mods_to
    modified_mods = [n for n in common_mods if resp_from[n] != resp_to[n]]

    # Test suite diff
    ts_from = get_test_suite(v_from.id)
    ts_to = get_test_suite(v_to.id)
    tests_from = {tc["id"] for tc in ts_from.test_cases} if ts_from else set()
    tests_to = {tc["id"] for tc in ts_to.test_cases} if ts_to else set()

    return VersionDiff(
        from_version=from_version_number,
        to_version=to_version_number,
        modules_added=sorted(mods_to - mods_from),
        modules_removed=sorted(mods_from - mods_to),
        modules_modified=sorted(modified_mods),
        interfaces_added=sorted(intfs_to - intfs_from),
        interfaces_removed=sorted(intfs_from - intfs_to),
        interfaces_modified=sorted(modified_intfs),
        tests_added=sorted(tests_to - tests_from),
        tests_removed=sorted(tests_from - tests_to),
        change_summary=v_to.change_summary,
    )


def _load_snapshot_modules_interfaces(
    snapshot_id: str | None,
) -> dict[str, list[dict]]:
    """Load modules and interfaces from a snapshot."""
    if not snapshot_id:
        return {"modules": [], "interfaces": []}
    db = get_db()
    cur = db.execute(
        "SELECT modules_json, interfaces_json FROM snapshots WHERE id = ?",
        (snapshot_id,),
    )
    row = cur.fetchone()
    if not row:
        return {"modules": [], "interfaces": []}
    return {
        "modules": json.loads(row["modules_json"] or "[]"),
        "interfaces": json.loads(row["interfaces_json"] or "[]"),
    }

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def rollback_to_version(
    project_id: str,
    target_version_number: int,
    reason: str = "manual rollback",
) -> WikiVersion:
    """Create a new version that reverts to a previous version's state.

    Does NOT delete history — creates a new version with the old snapshot
    and copies frozen openspec docs from the target version.
    """
    target = get_version_by_number(project_id, target_version_number)
    if target is None:
        raise ValueError(
            f"Version {target_version_number} not found for project {project_id}"
        )

    # Create new version pointing to the old snapshot
    new_version = create_version(
        project_id=project_id,
        snapshot_id=target.snapshot_id,
        change_type="rollback",
        change_summary=f"Rollback to v{target_version_number}: {reason}",
        change_source="rollback",
    )

    # Copy frozen openspec docs from target version
    with transaction() as c:
        c.execute(
            """UPDATE wiki_versions
            SET proposal_md = ?, design_md = ?, tasks_md = ?, spec_md = ?
            WHERE id = ?""",
            (target.proposal_md, target.design_md, target.tasks_md,
             target.spec_md, new_version.id),
        )

    # Update the returned object to reflect copied docs
    new_version.proposal_md = target.proposal_md
    new_version.design_md = target.design_md
    new_version.tasks_md = target.tasks_md
    new_version.spec_md = target.spec_md

    log_event(
        project_id, "wiki_rollback",
        f"Rolled back to v{target_version_number}: {reason}",
        version_id=new_version.id,
        event_data={
            "target_version": target_version_number,
            "target_version_id": target.id,
            "reason": reason,
        },
    )

    return new_version


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------


def log_event(
    project_id: str,
    event_type: str,
    event_detail: str,
    version_id: str | None = None,
    event_data: dict[str, Any] | None = None,
) -> None:
    """Write an entry to the changelog."""
    with transaction() as c:
        c.execute(
            """INSERT INTO wiki_changelog
            (id, project_id, version_id, event_type, event_detail,
             event_data_json, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (_uid(), project_id, version_id, event_type, event_detail,
             json.dumps(event_data) if event_data else None, _now()),
        )


def get_changelog(
    project_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Get recent changelog entries for a project."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM wiki_changelog WHERE project_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (project_id, limit),
    )
    return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Project summaries (multi-repo management)
# ---------------------------------------------------------------------------


def get_project_summary(project_id: str) -> dict:
    """Get project summary: latest version, test stats, health status."""
    from toyshop.storage.database import get_project, get_health_history

    project = get_project(project_id)
    if not project:
        return {"error": f"Project {project_id} not found"}

    latest = get_latest_version(project_id)
    test_suite = get_test_suite(latest.id) if latest else None
    health = get_health_history(project_id, limit=1)

    return {
        "project_id": project_id,
        "name": project.get("name", ""),
        "root_path": project.get("root_path", ""),
        "project_type": project.get("project_type", "python"),
        "language": project.get("language", "python"),
        "latest_version": latest.version_number if latest else 0,
        "git_commit": latest.git_commit_hash if latest else None,
        "total_tests": test_suite.total_tests if test_suite else 0,
        "tests_passed": test_suite.passed if test_suite else 0,
        "tests_failed": test_suite.failed if test_suite else 0,
        "health_warnings": health[0]["warning_count"] if health else None,
    }


def list_project_summaries() -> list[dict]:
    """List all projects with their latest version info."""
    from toyshop.storage.database import list_projects

    summaries = []
    for proj in list_projects():
        summaries.append(get_project_summary(proj["id"]))
    return summaries


# ---------------------------------------------------------------------------
# Test metadata extraction
# ---------------------------------------------------------------------------


def extract_test_metadata(
    workspace: Path,
    language: str = "python",
) -> tuple[list[str], list[dict[str, str]]]:
    """Scan tests/ directory and extract test case metadata.

    Delegates to the appropriate LanguageSupport implementation.

    Returns:
        (test_files, test_cases) where test_cases is a list of dicts with
        keys: id, name, file, class_name (empty string for top-level funcs).
    """
    from toyshop.lang.base import get_language_support
    import toyshop.lang.python_lang  # noqa: F401 — ensure registration

    lang = get_language_support(language)
    return lang.extract_test_metadata(workspace)


# ---------------------------------------------------------------------------
# Project bootstrap
# ---------------------------------------------------------------------------


def bootstrap_project(
    project_name: str,
    workspace: Path,
    project_type: str = "python",
    language: str = "python",
) -> tuple[str, "WikiVersion"]:
    """Bootstrap an existing codebase into the wiki system.

    1. Checks if project already exists by path (idempotent)
    2. Creates project record
    3. Scans code with snapshot.create_code_version()
    4. Converts snapshot to modules/interfaces for DB
    5. Creates initial wiki version (v1)
    6. Extracts test metadata and saves test suite
    7. Returns (project_id, version)
    """
    from toyshop.storage.database import (
        create_project, find_project_by_path,
        save_architecture_from_design,
    )
    from toyshop.snapshot import create_code_version

    # Idempotent: check if already bootstrapped
    existing = find_project_by_path(str(workspace))
    if existing:
        latest = get_latest_version(existing["id"])
        if latest:
            return existing["id"], latest

    # Create project
    proj = create_project(project_name, str(workspace), project_type, language)
    project_id = proj["id"]

    # Scan code
    snapshot = create_code_version(workspace, project_name)

    # Convert snapshot modules to DB format
    db_modules = []
    db_interfaces = []
    for mod in snapshot.modules:
        import uuid as _uuid
        mid = str(_uuid.uuid4())[:8]
        db_modules.append({
            "id": mid,
            "name": mod.name,
            "filePath": mod.file_path,
            "responsibilities": [],
            "dependencies": [],
        })
        for cls in mod.classes:
            db_interfaces.append({
                "id": str(_uuid.uuid4())[:8],
                "moduleId": mid,
                "name": cls.name,
                "type": "class",
                "signature": f"class {cls.name}({', '.join(cls.bases)})" if cls.bases else f"class {cls.name}",
            })
        for func in mod.functions:
            db_interfaces.append({
                "id": str(_uuid.uuid4())[:8],
                "moduleId": mid,
                "name": func.name,
                "type": "function",
                "signature": func.signature,
            })

    snapshot_id = None
    if db_modules or db_interfaces:
        snap = save_architecture_from_design(
            project_id, db_modules, db_interfaces, source="bootstrap",
        )
        snapshot_id = snap["id"]

    # Create v1
    version = create_version(
        project_id=project_id,
        snapshot_id=snapshot_id,
        change_type="create",
        change_summary=f"Bootstrap from existing codebase ({len(snapshot.modules)} modules)",
        change_source="bootstrap",
    )

    # Extract and save test metadata
    test_files, test_cases = extract_test_metadata(workspace, language)
    if test_files or test_cases:
        save_test_suite(
            version_id=version.id,
            test_files=test_files,
            test_cases=test_cases,
            total_tests=len(test_cases),
            passed=0,
            failed=0,
        )

    log_event(
        project_id, "version_created",
        f"Bootstrap v1: {len(snapshot.modules)} modules, {len(test_cases)} tests",
        version_id=version.id,
    )

    return project_id, version


def bootstrap_from_openspec(
    project_name: str,
    workspace: Path,
    openspec_dir: Path,
    project_type: str = "python",
    language: str = "python",
) -> tuple[str, "WikiVersion"]:
    """Bootstrap with existing openspec docs frozen into the initial version.

    Same as bootstrap_project but also freezes proposal.md, design.md,
    tasks.md, spec.md from the openspec directory.
    """
    from toyshop.storage.database import (
        create_project, find_project_by_path,
        save_architecture_from_design,
    )
    from toyshop.snapshot import create_code_version
    from toyshop.snapshot import _parse_design_modules, _parse_design_interfaces

    # Idempotent
    existing = find_project_by_path(str(workspace))
    if existing:
        latest = get_latest_version(existing["id"])
        if latest:
            return existing["id"], latest

    proj = create_project(project_name, str(workspace), project_type, language)
    project_id = proj["id"]

    # Parse design.md for structured architecture if available
    snapshot_id = None
    design_path = openspec_dir / "design.md"
    if design_path.exists():
        design_text = design_path.read_text(encoding="utf-8")
        modules = _parse_design_modules(design_text)
        interfaces = _parse_design_interfaces(design_text)

        import uuid as _uuid
        mod_name_to_id: dict[str, str] = {}
        db_modules = []
        for m in modules:
            mid = str(_uuid.uuid4())[:8]
            mod_name_to_id[m.get("name", "")] = mid
            db_modules.append({
                "id": mid,
                "name": m.get("name", ""),
                "filePath": m.get("filePath", ""),
                "responsibilities": m.get("responsibilities", []),
                "dependencies": m.get("dependencies", []),
            })
        db_interfaces = []
        for iface in interfaces:
            imod = iface.get("module", "")
            module_id = mod_name_to_id.get(imod, "")
            db_interfaces.append({
                "id": str(_uuid.uuid4())[:8],
                "moduleId": module_id,
                "name": iface.get("name", ""),
                "type": "class" if iface.get("signature", "").startswith("class ") else "function",
                "signature": iface.get("signature", ""),
            })
        if db_modules or db_interfaces:
            snap = save_architecture_from_design(
                project_id, db_modules, db_interfaces, source="bootstrap_openspec",
            )
            snapshot_id = snap["id"]

    version = create_version(
        project_id=project_id,
        snapshot_id=snapshot_id,
        change_type="create",
        change_summary=f"Bootstrap from openspec ({project_name})",
        change_source="bootstrap",
        openspec_dir=openspec_dir,
    )

    test_files, test_cases = extract_test_metadata(workspace, language)
    if test_files or test_cases:
        save_test_suite(
            version_id=version.id,
            test_files=test_files,
            test_cases=test_cases,
            total_tests=len(test_cases),
            passed=0,
            failed=0,
        )

    log_event(
        project_id, "version_created",
        f"Bootstrap from openspec v1: {len(test_cases)} tests",
        version_id=version.id,
    )

    return project_id, version
