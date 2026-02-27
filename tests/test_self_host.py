"""Tests for Stage 6: Self-hosting foundation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from toyshop.storage.database import (
    init_database, close_database, create_project,
    save_architecture_from_design,
    create_workflow_run, complete_workflow_run, get_workflow_runs,
    create_change_plan, get_change_plans,
)
from toyshop.storage.wiki import (
    create_version, get_latest_version, list_versions,
    get_version, rollback_to_version, get_test_suite,
)
from toyshop.self_host import (
    bootstrap_self, record_pipeline_run, generate_self_change_request,
    create_self_change_batch, apply_self_changes, commit_self_changes,
    resync_wiki, validate_no_protected_files, validate_self_hosting_integrity,
    PROTECTED_FILES,
    SelfApplyResult, _collect_changed_files, _parse_pytest_summary,
)
from toyshop.snapshot import (
    create_code_version, CodeVersion, bidirectional_drift_check,
)


@pytest.fixture(autouse=True)
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    yield db_path
    close_database()


@pytest.fixture
def project_id():
    proj = create_project("test-project", "/tmp/test")
    return proj["id"]


@pytest.fixture
def snapshot_id(project_id):
    snap = save_architecture_from_design(
        project_id,
        modules=[{"name": "core", "filePath": "app/core.py"}],
        interfaces=[{"name": "Calc", "type": "class", "signature": "class Calc:"}],
    )
    return snap["id"]


# ---------------------------------------------------------------------------
# Workflow runs CRUD
# ---------------------------------------------------------------------------


class TestWorkflowRuns:
    def test_create_and_get(self, project_id):
        run = create_workflow_run(project_id, "tdd_create", batch_id="b1")
        assert run["status"] == "running"
        assert run["workflow_type"] == "tdd_create"

        runs = get_workflow_runs(project_id)
        assert len(runs) == 1
        assert runs[0]["id"] == run["id"]

    def test_complete_workflow_run(self, project_id):
        run = create_workflow_run(project_id, "tdd_create")
        complete_workflow_run(run["id"], "completed", {"success": True, "summary": "all passed"})

        runs = get_workflow_runs(project_id)
        assert runs[0]["status"] == "completed"
        assert runs[0]["completed_at"] is not None
        assert runs[0]["result"]["success"] is True

    def test_failed_workflow_run(self, project_id):
        run = create_workflow_run(project_id, "tdd_modify")
        complete_workflow_run(run["id"], "failed", {"error": "tests failed"})

        runs = get_workflow_runs(project_id)
        assert runs[0]["status"] == "failed"
        assert runs[0]["result"]["error"] == "tests failed"

    def test_workflow_runs_ordering(self, project_id):
        r1 = create_workflow_run(project_id, "tdd_create")
        complete_workflow_run(r1["id"], "completed")
        r2 = create_workflow_run(project_id, "tdd_modify")
        complete_workflow_run(r2["id"], "completed")

        runs = get_workflow_runs(project_id)
        assert len(runs) == 2
        # Newest first
        assert runs[0]["id"] == r2["id"]


# ---------------------------------------------------------------------------
# Change plans CRUD
# ---------------------------------------------------------------------------


class TestChangePlans:
    def test_create_and_get(self, project_id):
        plan = create_change_plan(project_id, "Add caching layer")
        assert plan["status"] == "draft"
        assert plan["change_request"] == "Add caching layer"

        plans = get_change_plans(project_id)
        assert len(plans) == 1
        assert plans[0]["id"] == plan["id"]

    def test_filter_by_status(self, project_id):
        create_change_plan(project_id, "Plan A")
        create_change_plan(project_id, "Plan B")

        all_plans = get_change_plans(project_id)
        assert len(all_plans) == 2

        draft_plans = get_change_plans(project_id, status="draft")
        assert len(draft_plans) == 2

        active_plans = get_change_plans(project_id, status="active")
        assert len(active_plans) == 0


# ---------------------------------------------------------------------------
# record_pipeline_run
# ---------------------------------------------------------------------------


class TestRecordPipelineRun:
    def test_record_completed_run(self, project_id):
        run_id = record_pipeline_run(
            project_id, "tdd_create", batch_id="b1",
            result={"success": True, "summary": "5 passed"},
        )
        assert run_id

        runs = get_workflow_runs(project_id)
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["result"]["success"] is True

    def test_record_failed_run(self, project_id):
        run_id = record_pipeline_run(
            project_id, "tdd_modify",
            result={"success": False, "error": "3 failed"},
            status="failed",
        )
        runs = get_workflow_runs(project_id)
        assert runs[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# bootstrap_self
# ---------------------------------------------------------------------------


class TestBootstrapSelf:
    def test_bootstrap_self_creates_project(self, tmp_path):
        """bootstrap_self creates a project for ToyShop itself."""
        close_database()  # Close the autouse fixture DB

        db_path = tmp_path / "self.db"
        project_id = bootstrap_self(db_path=db_path)
        assert project_id

        # Verify project exists and has a version
        version = get_latest_version(project_id)
        assert version is not None
        assert version.version_number == 1

        close_database()

    def test_bootstrap_self_idempotent(self, tmp_path):
        """Calling bootstrap_self twice returns the same project_id."""
        close_database()

        db_path = tmp_path / "self.db"
        pid1 = bootstrap_self(db_path=db_path)
        close_database()

        pid2 = bootstrap_self(db_path=db_path)
        assert pid1 == pid2

        close_database()


# ---------------------------------------------------------------------------
# generate_self_change_request
# ---------------------------------------------------------------------------


class TestGenerateSelfChangeRequest:
    def test_generate_draft_without_llm(self, project_id, snapshot_id):
        version = create_version(project_id, snapshot_id, "create", "v1")

        result = generate_self_change_request(
            project_id, "Add rate limiting to API endpoints",
        )
        assert result["change_plan_id"]
        assert result["change_request"] == "Add rate limiting to API endpoints"
        assert result["status"] == "draft"
        assert result["version_id"] == version.id

    def test_generate_nonexistent_project_raises(self):
        with pytest.raises(ValueError, match="not found"):
            generate_self_change_request("nonexistent", "some change")

    def test_change_plan_persisted(self, project_id, snapshot_id):
        create_version(project_id, snapshot_id, "create", "v1")

        generate_self_change_request(project_id, "Refactor storage layer")

        plans = get_change_plans(project_id)
        assert len(plans) == 1
        assert plans[0]["change_request"] == "Refactor storage layer"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestParsePytestSummary:
    def test_passed_only(self):
        assert _parse_pytest_summary("409 passed in 8.77s") == (409, 0)

    def test_passed_and_failed(self):
        assert _parse_pytest_summary("394 passed, 15 failed") == (394, 15)

    def test_no_match(self):
        assert _parse_pytest_summary("no tests ran") == (0, 0)


class TestCollectChangedFiles:
    def test_detects_modified_file(self, tmp_path):
        orig = tmp_path / "orig" / "src"
        orig.mkdir(parents=True)
        (orig / "a.py").write_text("original")

        mod = tmp_path / "mod" / "src"
        mod.mkdir(parents=True)
        (mod / "a.py").write_text("modified")

        changed = _collect_changed_files(tmp_path / "orig", tmp_path / "mod", "src")
        assert "src/a.py" in changed

    def test_detects_new_file(self, tmp_path):
        orig = tmp_path / "orig" / "src"
        orig.mkdir(parents=True)
        (orig / "a.py").write_text("original")

        mod = tmp_path / "mod" / "src"
        mod.mkdir(parents=True)
        (mod / "a.py").write_text("original")
        (mod / "b.py").write_text("new file")

        changed = _collect_changed_files(tmp_path / "orig", tmp_path / "mod", "src")
        assert "src/b.py" in changed

    def test_no_changes(self, tmp_path):
        orig = tmp_path / "orig" / "src"
        orig.mkdir(parents=True)
        (orig / "a.py").write_text("same")

        mod = tmp_path / "mod" / "src"
        mod.mkdir(parents=True)
        (mod / "a.py").write_text("same")

        changed = _collect_changed_files(tmp_path / "orig", tmp_path / "mod", "src")
        assert changed == []

    def test_missing_subdir(self, tmp_path):
        orig = tmp_path / "orig"
        orig.mkdir()
        mod = tmp_path / "mod"
        mod.mkdir()
        assert _collect_changed_files(orig, mod, "nonexistent") == []


# ---------------------------------------------------------------------------
# SelfApplyResult serialization
# ---------------------------------------------------------------------------


class TestSelfApplyResult:
    def test_roundtrip_json(self, tmp_path):
        result = SelfApplyResult(
            success=True, staging_dir=tmp_path / "staging",
            changed_files=["toyshop/foo.py"], diff_text="--- a\n+++ b\n",
            test_total=10, test_passed=10, test_failed=0,
            test_output="10 passed", checkpoint_hash="abc123",
        )
        data = result.to_json()
        restored = SelfApplyResult.from_json(data)
        assert restored.success is True
        assert restored.staging_dir == tmp_path / "staging"
        assert restored.changed_files == ["toyshop/foo.py"]
        assert restored.test_passed == 10


# ---------------------------------------------------------------------------
# create_self_change_batch
# ---------------------------------------------------------------------------


class TestCreateSelfChangeBatch:
    def test_creates_batch_with_toyshop_source(self, tmp_path):
        batch = create_self_change_batch("Add logging", pm_root=tmp_path)
        ws = batch.batch_dir / "workspace"
        assert ws.is_dir()
        assert (ws / "toyshop" / "__init__.py").exists()
        assert (ws / "tests").is_dir()

    def test_batch_meta_has_self_modify_flag(self, tmp_path):
        batch = create_self_change_batch("Add logging", pm_root=tmp_path)
        meta = json.loads((batch.batch_dir / "batch_meta.json").read_text())
        assert meta["self_modify"] is True
        assert meta["type"] == "change"
        assert "source_root" in meta

    def test_workspace_no_git_dir(self, tmp_path):
        batch = create_self_change_batch("Add logging", pm_root=tmp_path)
        ws = batch.batch_dir / "workspace"
        assert not (ws / ".git").exists()

    def test_change_request_saved(self, tmp_path):
        batch = create_self_change_batch("Fix the bug in llm.py", pm_root=tmp_path)
        cr = (batch.batch_dir / "change_request.md").read_text()
        assert "Fix the bug in llm.py" in cr


# ---------------------------------------------------------------------------
# apply_self_changes
# ---------------------------------------------------------------------------


class TestApplySelfChanges:
    def _make_completed_batch(self, tmp_path, modify_file=None):
        """Create a minimal batch that looks like a completed self-change."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        # batch_meta.json
        (batch_dir / "batch_meta.json").write_text(json.dumps({
            "type": "change", "self_modify": True,
            "source_root": str(Path(__file__).resolve().parent.parent),
        }))

        # Copy real toyshop source as workspace
        from toyshop.self_host import _TOYSHOP_ROOT
        ws = batch_dir / "workspace"
        shutil.copytree(_TOYSHOP_ROOT, ws,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".toyshop"))

        # Optionally modify a file in workspace
        if modify_file:
            target = ws / modify_file
            if target.exists():
                content = target.read_text()
                target.write_text(content + "\n# self-modify test marker\n")

        # Minimal progress.json
        (batch_dir / "progress.json").write_text(json.dumps({
            "batch_id": "test_batch", "project_name": "toyshop",
            "status": "completed", "total_tasks": 0, "completed_tasks": 0,
            "failed_tasks": 0, "project_type": "python",
        }))

        # Create a mock BatchState
        from toyshop.pm import BatchState
        return BatchState(
            batch_id="test_batch", project_name="toyshop",
            batch_dir=batch_dir, status="completed", project_type="python",
        )

    def test_not_self_modify_batch_fails(self, tmp_path):
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()
        (batch_dir / "batch_meta.json").write_text(json.dumps({"type": "change"}))
        (batch_dir / "progress.json").write_text(json.dumps({
            "batch_id": "x", "project_name": "x", "status": "completed",
            "total_tasks": 0, "completed_tasks": 0, "failed_tasks": 0,
            "project_type": "python",
        }))
        from toyshop.pm import BatchState
        batch = BatchState(batch_id="x", project_name="x",
                           batch_dir=batch_dir, status="completed", project_type="python")
        result = apply_self_changes(batch)
        assert not result.success
        assert "Not a self-modify batch" in result.error

    def test_no_changes_empty_diff(self, tmp_path):
        batch = self._make_completed_batch(tmp_path)
        result = apply_self_changes(batch)
        assert result.success is True
        assert result.changed_files == []

    def test_changed_file_in_staging(self, tmp_path):
        batch = self._make_completed_batch(tmp_path, modify_file="toyshop/__init__.py")
        result = apply_self_changes(batch)
        assert "toyshop/__init__.py" in result.changed_files
        assert (result.staging_dir / "toyshop" / "__init__.py").exists()


# ---------------------------------------------------------------------------
# commit_self_changes
# ---------------------------------------------------------------------------


class TestCommitSelfChanges:
    def test_refuses_if_tests_failed(self):
        result = SelfApplyResult(
            success=False, staging_dir=Path("/tmp"), changed_files=["a.py"],
            diff_text="", test_total=10, test_passed=8, test_failed=2,
            test_output="", checkpoint_hash="",
        )
        from toyshop.pm import BatchState
        batch = BatchState(batch_id="x", project_name="x",
                           batch_dir=Path("/tmp"), status="completed", project_type="python")
        commit_result = commit_self_changes(batch, result)
        assert not commit_result["success"]
        assert "success is False" in commit_result["error"]

    def test_refuses_empty_changes(self):
        result = SelfApplyResult(
            success=True, staging_dir=Path("/tmp"), changed_files=[],
            diff_text="", test_total=10, test_passed=10, test_failed=0,
            test_output="", checkpoint_hash="",
        )
        from toyshop.pm import BatchState
        batch = BatchState(batch_id="x", project_name="x",
                           batch_dir=Path("/tmp"), status="completed", project_type="python")
        commit_result = commit_self_changes(batch, result)
        assert not commit_result["success"]
        assert "No changed files" in commit_result["error"]


# ---------------------------------------------------------------------------
# resync_wiki
# ---------------------------------------------------------------------------


class TestResyncWiki:
    def test_resync_creates_new_version(self, tmp_path):
        """resync_wiki creates a new wiki version after bootstrap."""
        close_database()
        db_path = tmp_path / "resync.db"
        project_id = bootstrap_self(db_path=db_path)

        v_before = get_latest_version(project_id)
        assert v_before is not None
        assert v_before.version_number == 1

        result = resync_wiki(change_summary="test resync")
        assert result["success"] is True
        assert result["version_number"] == 2
        assert result["modules"] > 0
        assert result["interfaces"] > 0

        v_after = get_latest_version(project_id)
        assert v_after.version_number == 2
        assert v_after.change_type == "modify"
        assert v_after.change_source == "self_modify"

        close_database()

    def test_resync_binds_commit_hash(self, tmp_path):
        """resync_wiki binds git commit hash when provided."""
        close_database()
        db_path = tmp_path / "resync.db"
        bootstrap_self(db_path=db_path)

        fake_hash = "abc123def456"
        result = resync_wiki(commit_hash=fake_hash)
        assert result["success"] is True

        from toyshop.storage.wiki import get_version
        v = get_version(result["version_id"])
        assert v.git_commit_hash == fake_hash

        close_database()

    def test_resync_without_project_fails(self):
        """resync_wiki returns error when no project exists."""
        result = resync_wiki()
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_resync_extracts_test_metadata(self, tmp_path):
        """resync_wiki saves test metadata for the new version."""
        close_database()
        db_path = tmp_path / "resync.db"
        bootstrap_self(db_path=db_path)

        result = resync_wiki()
        assert result["success"] is True
        assert result["test_files"] > 0
        assert result["test_cases"] > 0

        from toyshop.storage.wiki import get_test_suite
        ts = get_test_suite(result["version_id"])
        assert ts is not None
        assert ts.total_tests > 0

        close_database()

    def test_resync_increments_version(self, tmp_path):
        """Multiple resyncs increment version numbers correctly."""
        close_database()
        db_path = tmp_path / "resync.db"
        project_id = bootstrap_self(db_path=db_path)

        r1 = resync_wiki(change_summary="resync 1")
        assert r1["version_number"] == 2

        r2 = resync_wiki(change_summary="resync 2")
        assert r2["version_number"] == 3

        versions = list_versions(project_id)
        assert len(versions) == 3
        assert versions[0].version_number == 3
        assert versions[1].version_number == 2
        assert versions[2].version_number == 1

        close_database()


# ---------------------------------------------------------------------------
# Protected files
# ---------------------------------------------------------------------------


class TestProtectedFiles:
    def test_clean_files_no_violations(self):
        changed = ["toyshop/llm.py", "toyshop/pm.py", "tests/test_foo.py"]
        assert validate_no_protected_files(changed) == []

    def test_detects_protected_file(self):
        changed = ["toyshop/llm.py", "toyshop/self_host.py", "toyshop/pm.py"]
        violations = validate_no_protected_files(changed)
        assert "toyshop/self_host.py" in violations

    def test_detects_multiple_violations(self):
        changed = ["toyshop/self_host.py", "toyshop/snapshot.py", "toyshop/rollback.py"]
        violations = validate_no_protected_files(changed)
        assert len(violations) == 3


# ---------------------------------------------------------------------------
# Self-hosting integrity validation
# ---------------------------------------------------------------------------


class TestSelfHostingIntegrity:
    def test_valid_source_passes(self):
        from toyshop.self_host import _TOYSHOP_ROOT
        errors = validate_self_hosting_integrity(_TOYSHOP_ROOT)
        assert errors == []

    def test_missing_file_detected(self, tmp_path):
        errors = validate_self_hosting_integrity(tmp_path)
        assert any("file missing" in e for e in errors)

    def test_syntax_error_detected(self, tmp_path):
        target = tmp_path / "toyshop" / "self_host.py"
        target.parent.mkdir(parents=True)
        target.write_text("def broken(:\n  pass\n")
        errors = validate_self_hosting_integrity(tmp_path)
        assert any("SyntaxError" in e for e in errors)

    def test_missing_symbol_detected(self, tmp_path):
        target = tmp_path / "toyshop" / "self_host.py"
        target.parent.mkdir(parents=True)
        target.write_text("def some_other_func(): pass\n")
        errors = validate_self_hosting_integrity(tmp_path)
        assert any("missing required symbol" in e for e in errors)


# ---------------------------------------------------------------------------
# Wiki rollback
# ---------------------------------------------------------------------------


class TestWikiRollback:
    def test_rollback_creates_new_version(self, tmp_path):
        close_database()
        db_path = tmp_path / "rollback.db"
        project_id = bootstrap_self(db_path=db_path)
        resync_wiki(change_summary="v2 changes")

        rolled = rollback_to_version(project_id, 1, "test rollback")
        assert rolled.version_number == 3
        assert rolled.change_type == "rollback"
        assert "Rollback to v1" in rolled.change_summary
        close_database()

    def test_rollback_preserves_history(self, tmp_path):
        close_database()
        db_path = tmp_path / "rollback.db"
        project_id = bootstrap_self(db_path=db_path)
        resync_wiki(change_summary="v2")
        rollback_to_version(project_id, 1, "revert")

        versions = list_versions(project_id)
        assert len(versions) == 3
        assert versions[0].change_type == "rollback"
        assert versions[1].change_type == "modify"
        assert versions[2].change_type == "create"
        close_database()

    def test_rollback_copies_frozen_docs(self, tmp_path):
        close_database()
        db_path = tmp_path / "rollback.db"
        project_id = bootstrap_self(db_path=db_path)
        v1 = get_latest_version(project_id)
        resync_wiki(change_summary="v2")

        rolled = rollback_to_version(project_id, 1, "revert docs")
        assert rolled.proposal_md == v1.proposal_md
        assert rolled.design_md == v1.design_md
        close_database()

    def test_rollback_nonexistent_version_raises(self, tmp_path):
        close_database()
        db_path = tmp_path / "rollback.db"
        project_id = bootstrap_self(db_path=db_path)

        with pytest.raises(ValueError, match="not found"):
            rollback_to_version(project_id, 999, "bad")
        close_database()


# ---------------------------------------------------------------------------
# Bidirectional drift detection
# ---------------------------------------------------------------------------


class TestBidirectionalDrift:
    def test_detects_design_only(self):
        from toyshop.self_host import _TOYSHOP_ROOT
        cv = create_code_version(_TOYSHOP_ROOT, "toyshop")
        fake_design = "#### ZzzNonExistentWidget\n- **Signature:** `def zzz_non_existent() -> None`\n"
        result = bidirectional_drift_check(cv, fake_design)
        assert "ZzzNonExistentWidget" in result["design_only"] or "zzz_non_existent" in result["design_only"]

    def test_detects_code_only(self):
        from toyshop.self_host import _TOYSHOP_ROOT
        cv = create_code_version(_TOYSHOP_ROOT, "toyshop")
        result = bidirectional_drift_check(cv, "")
        assert len(result["code_only"]) > 0
        assert len(result["design_only"]) == 0

    def test_clean_state(self):
        cv = CodeVersion(project_name="test", root_path="/tmp", modules=[])
        result = bidirectional_drift_check(cv, "")
        assert result["design_only"] == []
        assert result["code_only"] == []
