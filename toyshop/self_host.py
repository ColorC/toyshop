"""Self-hosting foundation — ToyShop bootstraps itself into its own Wiki.

Provides:
- bootstrap_self(): Load ToyShop's own codebase into the wiki
- resync_wiki(): Re-scan codebase and create a new wiki version (maintenance)
- record_pipeline_run(): Track pipeline executions in workflow_runs
- generate_self_change_request(): Generate change requests against ToyShop's wiki state
- create_self_change_batch(): Create a brownfield batch targeting ToyShop itself
- run_self_pipeline(): Run the full brownfield pipeline for a self-change batch
- apply_self_changes(): Apply generated changes to staging + run self-tests
- commit_self_changes(): Commit approved changes to ToyShop's repo (auto-resyncs wiki)
"""

from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from toyshop.llm import LLM
    from toyshop.pm import BatchState

from toyshop.ports.llm import LLMPort


# Default ToyShop root (relative to this file)
_TOYSHOP_ROOT = Path(__file__).resolve().parent.parent

# Files that self-modify must NEVER touch (would break the self-hosting loop)
PROTECTED_FILES: frozenset[str] = frozenset({
    "toyshop/self_host.py",
    "toyshop/rollback.py",
    "toyshop/storage/database.py",
    "toyshop/storage/wiki.py",
    "toyshop/snapshot.py",
})


def validate_no_protected_files(changed_files: list[str]) -> list[str]:
    """Return list of violations (protected files that were modified)."""
    return [f for f in changed_files if f in PROTECTED_FILES]


# Key public functions that must exist in self-hosting code after modification
_REQUIRED_SYMBOLS: dict[str, list[str]] = {
    "toyshop/self_host.py": [
        "bootstrap_self", "resync_wiki", "apply_self_changes",
        "commit_self_changes", "SelfApplyResult",
    ],
    "toyshop/snapshot.py": ["create_code_version", "scan_python_file", "CodeVersion"],
    "toyshop/storage/wiki.py": ["create_version", "get_latest_version", "WikiVersion"],
    "toyshop/storage/database.py": ["init_database", "get_db", "transaction"],
    "toyshop/rollback.py": ["RollbackManager"],
}


def validate_self_hosting_integrity(staging_dir: Path) -> list[str]:
    """Validate that self-hosting code in staging is structurally sound.

    Uses ast.parse() to check:
    1. Protected files parse without SyntaxError
    2. Key public functions/classes still exist

    Returns list of error strings (empty = pass).
    """
    import ast as _ast

    errors: list[str] = []
    for rel_path, required in _REQUIRED_SYMBOLS.items():
        fpath = staging_dir / rel_path
        if not fpath.exists():
            errors.append(f"{rel_path}: file missing")
            continue
        try:
            source = fpath.read_text(encoding="utf-8")
            tree = _ast.parse(source)
        except SyntaxError as exc:
            errors.append(f"{rel_path}: SyntaxError at line {exc.lineno}")
            continue

        # Collect top-level names (functions, classes, assignments)
        defined: set[str] = set()
        for node in _ast.iter_child_nodes(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, _ast.Assign):
                for target in node.targets:
                    if isinstance(target, _ast.Name):
                        defined.add(target.id)

        for sym in required:
            if sym not in defined:
                errors.append(f"{rel_path}: missing required symbol '{sym}'")

    return errors


def bootstrap_self(
    db_path: str | Path | None = None,
    *,
    smart: bool = False,
    llm: "LLMPort | None" = None,
) -> str:
    """Bootstrap ToyShop itself into the wiki system.

    Args:
        db_path: Database path (default: .toyshop/architecture.db)
        smart: Use LLM-driven intelligent bootstrap
        llm: LLM instance (required if smart=True)

    Returns:
        The project_id.

    Idempotent — safe to call multiple times.
    """
    workspace = _TOYSHOP_ROOT

    if smart:
        if llm is None:
            raise ValueError("smart=True requires an LLM instance")
        from toyshop.smart_bootstrap import smart_bootstrap
        result = smart_bootstrap(
            project_name="toyshop",
            workspace=workspace,
            llm=llm,
            project_type="python",
            language="python",
            db_path=Path(db_path) if db_path else None,
        )
        return result.project_id

    # Fallback: existing dumb bootstrap
    from toyshop.storage.database import init_database
    from toyshop.storage.wiki import bootstrap_from_openspec, bootstrap_project

    if db_path is None:
        db_path = workspace / ".toyshop" / "architecture.db"
    init_database(db_path)

    # Check if openspec docs exist
    openspec_dir = workspace / "doc"
    if not openspec_dir.is_dir():
        openspec_dir = workspace / "openspec"

    if openspec_dir.is_dir() and (openspec_dir / "design.md").exists():
        project_id, _version = bootstrap_from_openspec(
            project_name="toyshop",
            workspace=workspace,
            openspec_dir=openspec_dir,
            project_type="python",
            language="python",
        )
    else:
        project_id, _version = bootstrap_project(
            project_name="toyshop",
            workspace=workspace,
            project_type="python",
            language="python",
        )

    return project_id


def resync_wiki(
    commit_hash: str | None = None,
    change_summary: str = "Resync after self-modification",
    change_source: str = "self_modify",
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Re-scan ToyShop's codebase and create a new wiki version.

    This is the maintenance mechanism that keeps the wiki in sync with
    actual source code after self-modifications (or any code change).

    Steps:
        1. Resolve ToyShop's project_id from the DB
        2. Re-scan the source tree with create_code_version()
        3. Convert snapshot to DB format and save as new architecture snapshot
        4. Create a new wiki version pointing to the new snapshot
        5. Bind the git commit hash (if provided)
        6. Extract and save test metadata
        7. Run drift detection against design.md
        8. Log the resync event

    Returns:
        Dict with version_id, version_number, snapshot_id, drift_warnings.
    """
    from toyshop.storage.database import (
        find_project_by_path,
        save_architecture_from_design,
    )
    from toyshop.storage.wiki import (
        create_version,
        bind_git_commit,
        save_test_suite,
        extract_test_metadata,
        get_latest_version,
        log_event,
    )
    from toyshop.snapshot import create_code_version, bidirectional_drift_check

    workspace = _TOYSHOP_ROOT

    # 1. Resolve project
    project = find_project_by_path(str(workspace))
    if not project:
        return {"success": False, "error": "ToyShop project not found in wiki DB"}
    project_id = project["id"]

    # 2. Re-scan source tree
    snapshot = create_code_version(workspace, "toyshop")

    # 3. Convert to DB format and save
    import uuid as _uuid
    db_modules = []
    db_interfaces = []
    for mod in snapshot.modules:
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
                "signature": (
                    f"class {cls.name}({', '.join(cls.bases)})"
                    if cls.bases else f"class {cls.name}"
                ),
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
            project_id, db_modules, db_interfaces, source="resync",
        )
        snapshot_id = snap["id"]

    # 4. Create new wiki version
    latest = get_latest_version(project_id)
    openspec_dir = workspace / "doc"
    if not openspec_dir.is_dir():
        openspec_dir = workspace / "openspec"

    version = create_version(
        project_id=project_id,
        snapshot_id=snapshot_id,
        change_type="modify",
        change_summary=change_summary,
        change_source=change_source,
        batch_id=batch_id,
        openspec_dir=openspec_dir if openspec_dir.is_dir() else None,
    )

    # 5. Bind git commit
    if commit_hash:
        bind_git_commit(version.id, commit_hash)

    # 6. Extract and save test metadata
    test_files, test_cases = extract_test_metadata(workspace, "python")
    if test_files or test_cases:
        save_test_suite(
            version_id=version.id,
            test_files=test_files,
            test_cases=test_cases,
            total_tests=len(test_cases),
            passed=0,
            failed=0,
        )

    # 7. Drift detection (bidirectional)
    drift_warnings: list[str] = []
    drift_detail: dict[str, list[str]] = {}
    if latest and latest.design_md:
        drift_detail = bidirectional_drift_check(snapshot, latest.design_md)
        for name in drift_detail.get("design_only", []):
            drift_warnings.append(f"design.md 接口 {name} 在代码中未找到")
        for name in drift_detail.get("code_only", []):
            drift_warnings.append(f"代码接口 {name} 在 design.md 中未定义")

    # 8. Log
    log_event(
        project_id, "wiki_resync",
        f"Resync v{version.version_number}: {len(snapshot.modules)} modules, "
        f"{len(db_interfaces)} interfaces, {len(drift_warnings)} drift warnings",
        version_id=version.id,
        event_data={
            "modules": len(snapshot.modules),
            "interfaces": len(db_interfaces),
            "drift_warnings": drift_warnings,
            "commit_hash": commit_hash,
        },
    )

    print(
        f"[self-host] Wiki resync → v{version.version_number}: "
        f"{len(snapshot.modules)} modules, {len(db_interfaces)} interfaces"
    )
    if drift_warnings:
        print(f"[self-host] Drift warnings ({len(drift_warnings)}):")
        for w in drift_warnings[:5]:
            print(f"  - {w}")

    return {
        "success": True,
        "project_id": project_id,
        "version_id": version.id,
        "version_number": version.version_number,
        "snapshot_id": snapshot_id,
        "modules": len(snapshot.modules),
        "interfaces": len(db_interfaces),
        "test_files": len(test_files),
        "test_cases": len(test_cases),
        "drift_warnings": drift_warnings,
    }


def record_pipeline_run(
    project_id: str,
    workflow_type: str,
    batch_id: str | None = None,
    result: dict[str, Any] | None = None,
    status: str = "completed",
) -> str:
    """Record a pipeline run to workflow_runs.

    Args:
        project_id: The project this run belongs to
        workflow_type: "tdd_create" | "tdd_modify" | "change_pipeline" | "bootstrap"
        batch_id: Optional batch ID linking to PM batch
        result: Optional result dict (success, summary, etc.)
        status: Final status — "completed" | "failed"

    Returns:
        The workflow run ID.
    """
    from toyshop.storage.database import create_workflow_run, complete_workflow_run

    run = create_workflow_run(project_id, workflow_type, batch_id)
    complete_workflow_run(run["id"], status, result)
    return run["id"]


def generate_self_change_request(
    project_id: str,
    description: str,
    llm: "LLMPort | None" = None,
) -> dict[str, Any]:
    """Generate a structured change request for ToyShop itself.

    Uses the wiki's current state (latest version, architecture snapshot)
    to produce a change plan that can be fed into the TDD pipeline.

    Args:
        project_id: ToyShop's project ID in the wiki
        description: Natural language description of the desired change
        llm: Optional LLM for impact analysis (if None, returns draft only)

    Returns:
        Dict with change_plan_id, change_request, and optionally impact analysis.
    """
    from toyshop.storage.database import (
        get_project, create_change_plan, get_latest_snapshot,
    )
    from toyshop.storage.wiki import get_latest_version

    project = get_project(project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    latest = get_latest_version(project_id)
    version_id = latest.id if latest else None

    # Create the change plan record
    plan = create_change_plan(
        project_id=project_id,
        change_request=description,
        version_id=version_id,
    )

    result: dict[str, Any] = {
        "change_plan_id": plan["id"],
        "project_id": project_id,
        "change_request": description,
        "version_id": version_id,
        "status": "draft",
    }

    # If LLM provided, run impact analysis
    if llm is not None:
        snapshot = get_latest_snapshot(project_id)
        design_md = latest.design_md if latest and latest.design_md else ""
        spec_md = latest.spec_md if latest and latest.spec_md else ""

        if snapshot and design_md:
            from toyshop.snapshot import create_code_version
            from toyshop.impact import run_impact_analysis, save_impact

            # Build a CodeVersion from the stored snapshot data
            code_snapshot = create_code_version(
                Path(project["root_path"]),
                project["name"],
            )

            impact = run_impact_analysis(
                change_request=description,
                snapshot=code_snapshot,
                design_md=design_md,
                spec_md=spec_md,
                llm=llm,
            )
            result["impact"] = {
                "change_summary": impact.change_summary,
                "affected_modules": len(impact.affected_modules),
                "affected_interfaces": len(impact.affected_interfaces),
                "new_modules": len(impact.new_modules),
            }

    return result


# ---------------------------------------------------------------------------
# Self-modification pipeline
# ---------------------------------------------------------------------------

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".pytest_cache", "*.pyc", ".toyshop",
)

# Minimum expected test count — if staging run collects fewer, something broke.
_MIN_TEST_RATIO = 0.8


@dataclass
class SelfApplyResult:
    """Result of applying self-changes to a staging copy."""

    success: bool
    staging_dir: Path
    changed_files: list[str]
    diff_text: str
    test_total: int
    test_passed: int
    test_failed: int
    test_output: str
    checkpoint_hash: str
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["staging_dir"] = str(d["staging_dir"])
        return d

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SelfApplyResult":
        data["staging_dir"] = Path(data["staging_dir"])
        return cls(**data)


def create_self_change_batch(
    change_request: str,
    pm_root: str | Path | None = None,
) -> "BatchState":
    """Create a brownfield change batch targeting ToyShop's own codebase.

    Wraps create_change_batch() with ToyShop-specific defaults.
    After copying, removes .git from workspace to avoid confusion
    with the source repo (RollbackManager will re-init during TDD).
    """
    from toyshop.pm import create_change_batch

    if pm_root is None:
        pm_root = Path.home() / ".toyshop" / "self_changes"

    batch = create_change_batch(
        pm_root, "toyshop", _TOYSHOP_ROOT, change_request,
        project_type="python",
    )

    # Clean up copied .git and caches from workspace
    ws = batch.batch_dir / "workspace"
    for cleanup in [".git", "__pycache__", ".pytest_cache", ".toyshop"]:
        target = ws / cleanup
        if target.is_dir():
            shutil.rmtree(target)
    # Recursively remove __pycache__ in subdirs
    for pycache in ws.rglob("__pycache__"):
        shutil.rmtree(pycache)

    # Tag batch as self-modify
    meta_path = batch.batch_dir / "batch_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["self_modify"] = True
    meta["source_root"] = str(_TOYSHOP_ROOT)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"[self-host] Created self-change batch: {batch.batch_dir}")
    return batch


def run_self_pipeline(
    batch: "BatchState",
    llm: "LLMPort",
) -> "BatchState":
    """Run the full brownfield pipeline for a self-change batch.

    Orchestrates: change-analyze → change-spec → tdd (modify mode).
    """
    from toyshop.pm import run_change_analysis, run_spec_evolution, run_batch_tdd

    print("[self-host] Running self-modification pipeline...")

    # Phase 1+2: Snapshot + impact analysis
    impact = run_change_analysis(batch, llm)

    # Phase 3: Evolve openspec
    batch = run_spec_evolution(batch, impact, llm)
    if batch.status == "failed":
        return batch

    # Phase 4: TDD pipeline (modify mode)
    run_batch_tdd(batch, llm, mode="modify")

    print(f"[self-host] Pipeline finished: {batch.status}")
    return batch


def _collect_changed_files(
    original: Path,
    modified: Path,
    subdir: str,
) -> list[str]:
    """Recursively find files that differ between original/subdir and modified/subdir."""
    orig_sub = original / subdir
    mod_sub = modified / subdir
    if not orig_sub.is_dir() or not mod_sub.is_dir():
        return []

    changed: list[str] = []

    def _walk(dcmp: filecmp.dircmp, prefix: str) -> None:
        for name in dcmp.diff_files:
            changed.append(f"{prefix}{name}")
        for name in dcmp.right_only:
            changed.append(f"{prefix}{name}")
        for sub_name, sub_dcmp in dcmp.subdirs.items():
            if sub_name in ("__pycache__", ".pytest_cache", ".git"):
                continue
            _walk(sub_dcmp, f"{prefix}{sub_name}/")

    dcmp = filecmp.dircmp(orig_sub, mod_sub, ignore=["__pycache__", ".pytest_cache", ".git"])
    # filecmp.dircmp uses shallow comparison by default.
    # Re-check "same" files with deep (content) comparison to catch modifications.
    _walk(dcmp, f"{subdir}/")

    # Deep-compare files that dircmp reported as "same" (shallow match only)
    def _deep_check(dcmp: filecmp.dircmp, prefix: str) -> None:
        if dcmp.same_files:
            # Compare content, not just stat
            match, mismatch, errors = filecmp.cmpfiles(
                dcmp.left, dcmp.right, dcmp.same_files, shallow=False,
            )
            for name in mismatch:
                changed.append(f"{prefix}{name}")
        for sub_name, sub_dcmp in dcmp.subdirs.items():
            if sub_name in ("__pycache__", ".pytest_cache", ".git"):
                continue
            _deep_check(sub_dcmp, f"{prefix}{sub_name}/")

    _deep_check(dcmp, f"{subdir}/")
    return changed


def _parse_pytest_summary(output: str) -> tuple[int, int]:
    """Extract (passed, failed) counts from pytest output."""
    passed = failed = 0
    m = re.search(r"(\d+)\s+passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", output)
    if m:
        failed = int(m.group(1))
    return passed, failed


def apply_self_changes(batch: "BatchState") -> SelfApplyResult:
    """Apply generated changes to a staging copy and run ToyShop's test suite.

    1. Copies the ORIGINAL source tree into staging/
    2. Overlays changed files from the batch workspace
    3. Runs pytest with PYTHONPATH=staging
    4. Returns results for human review
    """
    # Validate this is a self-modify batch
    meta_path = batch.batch_dir / "batch_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not meta.get("self_modify"):
        return SelfApplyResult(
            success=False, staging_dir=Path("."), changed_files=[],
            diff_text="", test_total=0, test_passed=0, test_failed=0,
            test_output="", checkpoint_hash="",
            error="Not a self-modify batch",
        )

    workspace = batch.batch_dir / "workspace"
    staging_dir = batch.batch_dir / "staging"

    # Clean previous staging if exists
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    # Copy original source tree to staging (excluding .git etc.)
    print("[self-host] Creating staging copy...")
    shutil.copytree(_TOYSHOP_ROOT, staging_dir, ignore=_COPY_IGNORE)

    # Find changed files
    changed_files: list[str] = []
    for subdir in ("toyshop", "tests"):
        changed_files.extend(_collect_changed_files(_TOYSHOP_ROOT, workspace, subdir))

    # Reject if protected files were modified
    violations = validate_no_protected_files(changed_files)
    if violations:
        print(f"[self-host] BLOCKED: protected files modified: {violations}")
        return SelfApplyResult(
            success=False, staging_dir=staging_dir, changed_files=changed_files,
            diff_text="", test_total=0, test_passed=0, test_failed=0,
            test_output="", checkpoint_hash="",
            error=f"Protected files modified: {violations}",
        )

    if not changed_files:
        print("[self-host] No changes detected in workspace.")
        return SelfApplyResult(
            success=True, staging_dir=staging_dir, changed_files=[],
            diff_text="", test_total=0, test_passed=0, test_failed=0,
            test_output="No changes to test.", checkpoint_hash="",
        )

    # Overlay changed files onto staging
    print(f"[self-host] Applying {len(changed_files)} changed files to staging...")
    for rel_path in changed_files:
        src = workspace / rel_path
        dst = staging_dir / rel_path
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Generate unified diff
    diff_result = subprocess.run(
        ["diff", "-ruN",
         "--exclude=__pycache__", "--exclude=.pytest_cache", "--exclude=.git",
         str(_TOYSHOP_ROOT / "toyshop"), str(staging_dir / "toyshop")],
        capture_output=True, text=True, timeout=30,
    )
    diff_text = diff_result.stdout

    # Also diff tests/ if changed
    if any(f.startswith("tests/") for f in changed_files):
        tests_diff = subprocess.run(
            ["diff", "-ruN",
             "--exclude=__pycache__", "--exclude=.pytest_cache",
             str(_TOYSHOP_ROOT / "tests"), str(staging_dir / "tests")],
            capture_output=True, text=True, timeout=30,
        )
        diff_text += tests_diff.stdout

    # Validate self-hosting code integrity before running tests
    integrity_errors = validate_self_hosting_integrity(staging_dir)
    if integrity_errors:
        print("[self-host] BLOCKED: self-hosting integrity check failed:")
        for err in integrity_errors:
            print(f"  - {err}")
        return SelfApplyResult(
            success=False, staging_dir=staging_dir, changed_files=changed_files,
            diff_text=diff_text, test_total=0, test_passed=0, test_failed=0,
            test_output="", checkpoint_hash="",
            error=f"Self-hosting integrity check failed: {integrity_errors}",
        )

    # Run self-tests against staging
    print("[self-host] Running self-tests against staging...")
    test_env = {**os.environ, "PYTHONPATH": str(staging_dir)}
    try:
        test_result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "-v", "--tb=short",
             "--ignore=tests/run_*", "-q"],
            cwd=staging_dir,
            env=test_env,
            capture_output=True, text=True, timeout=300,
        )
        test_output = test_result.stdout + test_result.stderr
    except subprocess.TimeoutExpired:
        return SelfApplyResult(
            success=False, staging_dir=staging_dir, changed_files=changed_files,
            diff_text=diff_text, test_total=0, test_passed=0, test_failed=0,
            test_output="pytest timed out (300s)", checkpoint_hash="",
            error="Test timeout",
        )

    passed, failed = _parse_pytest_summary(test_output)
    total = passed + failed

    # Sanity check: test count shouldn't drop dramatically
    error = None
    success = failed == 0 and passed > 0
    if total > 0 and total < 100 * _MIN_TEST_RATIO:
        error = f"Test count suspiciously low: {total} (expected ~400+)"
        success = False

    result = SelfApplyResult(
        success=success,
        staging_dir=staging_dir,
        changed_files=changed_files,
        diff_text=diff_text,
        test_total=total,
        test_passed=passed,
        test_failed=failed,
        test_output=test_output,
        checkpoint_hash="",  # No git checkpoint in staging
        error=error,
    )

    # Persist result
    result_path = batch.batch_dir / "self_apply_result.json"
    result_path.write_text(json.dumps(result.to_json(), indent=2), encoding="utf-8")

    status = "PASS" if success else "FAIL"
    print(f"[self-host] Self-test {status}: {passed} passed, {failed} failed")
    return result


def commit_self_changes(
    batch: "BatchState",
    apply_result: SelfApplyResult,
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Commit approved self-changes to ToyShop's actual source tree.

    Safety:
    - Creates git checkpoint before any changes
    - Copies changed files from staging to source tree
    - Runs pytest one more time as final verification
    - Rolls back on failure

    Returns dict with success, commit_hash, test results.
    """
    if not apply_result.success:
        return {"success": False, "error": "Cannot commit: apply_result.success is False"}
    if apply_result.test_failed > 0:
        return {"success": False, "error": f"Cannot commit: {apply_result.test_failed} tests failed"}
    if not apply_result.changed_files:
        return {"success": False, "error": "No changed files to commit"}

    from toyshop.rollback import RollbackManager

    rollback = RollbackManager(_TOYSHOP_ROOT)
    checkpoint = rollback.checkpoint("pre-self-commit")
    print(f"[self-host] Git checkpoint: {checkpoint[:8]}")

    staging_dir = apply_result.staging_dir

    # Copy changed files from staging to source tree
    for rel_path in apply_result.changed_files:
        src = staging_dir / rel_path
        dst = _TOYSHOP_ROOT / rel_path
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  {rel_path}")

    # Final verification: run tests against the real source tree
    print("[self-host] Final verification...")
    try:
        test_result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "-v", "--tb=short",
             "--ignore=tests/run_*", "-q"],
            cwd=_TOYSHOP_ROOT,
            capture_output=True, text=True, timeout=300,
        )
        passed, failed = _parse_pytest_summary(test_result.stdout + test_result.stderr)
    except subprocess.TimeoutExpired:
        print("[self-host] Final tests timed out — rolling back!")
        rollback.rollback_to(checkpoint)
        return {"success": False, "error": "Final test timeout, rolled back"}

    if failed > 0:
        print(f"[self-host] Final tests FAILED ({failed} failures) — rolling back!")
        rollback.rollback_to(checkpoint)
        return {
            "success": False,
            "error": f"Final verification failed: {failed} tests failed, rolled back to {checkpoint[:8]}",
            "test_passed": passed,
            "test_failed": failed,
        }

    # Tests pass — commit
    if commit_message is None:
        commit_message = f"self-modify: {batch.project_name} ({len(apply_result.changed_files)} files)"

    rollback._run_git("add", "-A")
    rollback._run_git("commit", "-m", commit_message)
    commit_hash = rollback._run_git("rev-parse", "HEAD")

    # Resync wiki: re-scan codebase, create new version, bind commit
    resync_result: dict[str, Any] = {}
    try:
        resync_result = resync_wiki(
            commit_hash=commit_hash,
            change_summary=commit_message,
            change_source="self_modify",
            batch_id=batch.batch_id,
        )
    except Exception as exc:
        # Resync is important but not worth rolling back a successful commit
        print(f"[self-host] Wiki resync failed (non-fatal): {exc}")
        resync_result = {"success": False, "error": str(exc)}

    print(f"[self-host] Committed: {commit_hash[:8]} ({passed} tests passed)")
    return {
        "success": True,
        "commit_hash": commit_hash,
        "checkpoint_hash": checkpoint,
        "files_changed": apply_result.changed_files,
        "test_passed": passed,
        "test_failed": 0,
        "wiki_resync": resync_result,
    }
