"""Tests for Stage 6: Self-hosting foundation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toyshop.storage.database import (
    init_database, close_database, create_project,
    save_architecture_from_design,
    create_workflow_run, complete_workflow_run, get_workflow_runs,
    create_change_plan, get_change_plans,
)
from toyshop.storage.wiki import create_version, get_latest_version
from toyshop.self_host import bootstrap_self, record_pipeline_run, generate_self_change_request


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
