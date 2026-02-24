from __future__ import annotations

import json
from pathlib import Path

import pytest

from toyshop.pm import create_batch, run_batch_phased, run_research_planning, _build_stage_requirement, approve_review, reject_review
from toyshop.research_agent import ResearchPlan
from toyshop.tdd_pipeline import TDDResult


def _ok_tdd_result(summary: str) -> TDDResult:
    return TDDResult(
        success=True,
        whitebox_passed=True,
        blackbox_passed=True,
        summary=summary,
    )


def test_run_batch_phased_auto_continue(monkeypatch, tmp_path: Path):
    calls: list[str] = []

    def fake_run_research_planning(batch, llm, **kwargs):
        return ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build auth system",
            mvp_option="MVP option",
            sota_option="SOTA option",
            mvp_scope=["auth", "api"],
            tradeoffs=["speed vs quality"],
            adoption_plan=["stage_1_mvp", "checkpoint_mvp_uploaded", "stage_2_sota"],
        )

    def fake_run_spec_generation(batch, llm, **kwargs):
        batch.status = "in_progress"
        batch.error = None
        openspec_dir = batch.batch_dir / "openspec"
        openspec_dir.mkdir(exist_ok=True)
        for f in ("proposal.md", "design.md", "tasks.md", "spec.md"):
            (openspec_dir / f).write_text(f"# {f}\n", encoding="utf-8")
        return batch

    def fake_prepare_tasks(batch):
        return []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        calls.append(mode)
        batch.status = "completed"
        batch.error = None
        return _ok_tdd_result(summary=f"{mode} ok")

    monkeypatch.setattr("toyshop.pm.run_research_planning", fake_run_research_planning)
    monkeypatch.setattr("toyshop.pm.run_spec_generation", fake_run_spec_generation)
    monkeypatch.setattr("toyshop.pm.prepare_tasks", fake_prepare_tasks)
    monkeypatch.setattr("toyshop.pm.run_batch_tdd", fake_run_batch_tdd)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build auth system",
        llm=object(),
        auto_continue_sota=True,
        enable_research_agent=True,
    )

    assert batch.status == "completed"
    assert calls == ["create", "modify"]

    checkpoint = json.loads((batch.batch_dir / "stage_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["current_stage"] == "done"
    assert checkpoint["stage_gate_passed"] is True
    assert (batch.batch_dir / "mid_report_hook.json").exists()
    assert (batch.batch_dir / "phase_results.json").exists()
    assert (batch.batch_dir / "quality_gates.json").exists()
    exit_conditions = json.loads((batch.batch_dir / "exit_conditions.json").read_text(encoding="utf-8"))
    assert exit_conditions["passed"] is True


def test_run_batch_phased_stop_after_mvp(monkeypatch, tmp_path: Path):
    calls: list[str] = []

    def fake_run_spec_generation(batch, llm, **kwargs):
        batch.status = "in_progress"
        batch.error = None
        openspec_dir = batch.batch_dir / "openspec"
        openspec_dir.mkdir(exist_ok=True)
        for f in ("proposal.md", "design.md", "tasks.md", "spec.md"):
            (openspec_dir / f).write_text(f"# {f}\n", encoding="utf-8")
        return batch

    def fake_prepare_tasks(batch):
        return []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        calls.append(mode)
        batch.status = "completed"
        batch.error = None
        return _ok_tdd_result(summary=f"{mode} ok")

    monkeypatch.setattr("toyshop.pm.run_spec_generation", fake_run_spec_generation)
    monkeypatch.setattr("toyshop.pm.prepare_tasks", fake_prepare_tasks)
    monkeypatch.setattr("toyshop.pm.run_batch_tdd", fake_run_batch_tdd)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build auth system",
        llm=object(),
        auto_continue_sota=False,
        enable_research_agent=False,
    )

    assert batch.status == "completed"
    assert calls == ["create"]

    checkpoint = json.loads((batch.batch_dir / "stage_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["current_stage"] == "done"
    assert (batch.batch_dir / "mid_report_hook.json").exists()
    assert not (batch.batch_dir / "phase_results.json").exists()
    assert (batch.batch_dir / "quality_gates.json").exists()
    exit_conditions = json.loads((batch.batch_dir / "exit_conditions.json").read_text(encoding="utf-8"))
    assert exit_conditions["passed"] is True
    assert "mvp_completed_stop_after_mvp" in exit_conditions["reasons"]


def test_build_stage_requirement_contains_stage_context():
    plan = ResearchPlan(
        trigger_type="kickoff_mvp_sota",
        problem_statement="build auth",
        mvp_option="MVP",
        sota_option="SOTA",
        mvp_scope=["auth", "api"],
    )
    mvp_req = _build_stage_requirement("build auth", plan, "mvp")
    sota_req = _build_stage_requirement("build auth", plan, "sota")
    assert "Stage Target: MVP" in mvp_req
    assert "MVP Scope" in mvp_req
    assert "Stage Target: SOTA" in sota_req


def test_run_batch_phased_mvp_deadlock_resolution_retry(monkeypatch, tmp_path: Path):
    calls: list[str] = []
    trigger_calls: list[str] = []

    def fake_run_research_planning(batch, llm, **kwargs):
        trigger = kwargs.get("trigger_type", "kickoff_mvp_sota")
        trigger_calls.append(trigger)
        return ResearchPlan(
            trigger_type=trigger,
            problem_statement="build auth system",
            mvp_option=f"{trigger} MVP option",
            sota_option=f"{trigger} SOTA option",
            mvp_scope=["auth", "api"],
            tradeoffs=["speed vs quality"],
            adoption_plan=["stage_1_mvp", "checkpoint_mvp_uploaded", "stage_2_sota"],
        )

    def fake_run_spec_generation(batch, llm, **kwargs):
        batch.status = "in_progress"
        batch.error = None
        openspec_dir = batch.batch_dir / "openspec"
        openspec_dir.mkdir(exist_ok=True)
        for f in ("proposal.md", "design.md", "tasks.md", "spec.md"):
            (openspec_dir / f).write_text(f"# {f}\n", encoding="utf-8")
        return batch

    def fake_prepare_tasks(batch):
        return []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        calls.append(mode)
        # First MVP attempt fails, retry succeeds.
        if mode == "create" and calls.count("create") == 1:
            batch.status = "failed"
            batch.error = "mvp failed first attempt"
            return TDDResult(
                success=False,
                whitebox_passed=False,
                blackbox_passed=False,
                summary="mvp failed first attempt",
            )
        batch.status = "completed"
        batch.error = None
        return _ok_tdd_result(summary=f"{mode} ok")

    monkeypatch.setattr("toyshop.pm.run_research_planning", fake_run_research_planning)
    monkeypatch.setattr("toyshop.pm.run_spec_generation", fake_run_spec_generation)
    monkeypatch.setattr("toyshop.pm.prepare_tasks", fake_prepare_tasks)
    monkeypatch.setattr("toyshop.pm.run_batch_tdd", fake_run_batch_tdd)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build auth system",
        llm=object(),
        auto_continue_sota=False,
        enable_research_agent=True,
    )

    assert batch.status == "completed"
    assert calls == ["create", "create"]
    assert trigger_calls == ["kickoff_mvp_sota", "deadlock_resolution"]

    phase_results = batch.batch_dir / "phase_results.json"
    assert not phase_results.exists()

    lines = (batch.batch_dir / "stage_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"event": "deadlock_resolution_start"' in ln for ln in lines)
    assert any('"event": "deadlock_resolution_done"' in ln for ln in lines)

    gates = json.loads((batch.batch_dir / "quality_gates.json").read_text(encoding="utf-8"))
    mvp_tdd_gates = [g for g in gates if g["stage"] == "mvp" and g["gate"] == "tdd"]
    assert len(mvp_tdd_gates) == 2
    assert mvp_tdd_gates[0]["passed"] is False
    assert mvp_tdd_gates[1]["passed"] is True


def test_run_research_planning_rejects_unknown_trigger(tmp_path: Path):
    batch = create_batch(tmp_path, "demo", "build auth system")
    with pytest.raises(ValueError):
        run_research_planning(batch, llm=object(), trigger_type="unsupported_trigger")


# ---------------------------------------------------------------------------
# Stage 3: Review checkpoint tests
# ---------------------------------------------------------------------------


def test_run_batch_phased_pauses_at_research_review(monkeypatch, tmp_path: Path):
    """With auto_approve_research=False, pipeline pauses after research."""

    def fake_run_research_planning(batch, llm, **kwargs):
        return ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build auth",
            mvp_option="MVP",
            sota_option="SOTA",
            mvp_scope=["auth"],
        )

    monkeypatch.setattr("toyshop.pm.run_research_planning", fake_run_research_planning)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build auth system",
        llm=object(),
        enable_research_agent=True,
        auto_approve_research=False,
    )

    assert batch.status == "awaiting_review"
    cp_path = batch.batch_dir / "review_checkpoint.json"
    assert cp_path.exists()
    cp = json.loads(cp_path.read_text(encoding="utf-8"))
    assert cp["checkpoint_type"] == "research_review"
    assert cp["status"] == "pending"
    assert "research/summary.md" in cp["artifacts_to_review"]


def test_approve_review_updates_checkpoint(monkeypatch, tmp_path: Path):
    """approve_review marks checkpoint as approved."""

    def fake_run_research_planning(batch, llm, **kwargs):
        return ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build auth",
            mvp_option="MVP",
            sota_option="SOTA",
            mvp_scope=["auth"],
        )

    monkeypatch.setattr("toyshop.pm.run_research_planning", fake_run_research_planning)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build auth system",
        llm=object(),
        enable_research_agent=True,
        auto_approve_research=False,
    )

    approve_review(batch.batch_dir, reviewer_notes="Looks good")
    cp = json.loads((batch.batch_dir / "review_checkpoint.json").read_text(encoding="utf-8"))
    assert cp["status"] == "approved"
    assert cp["reviewer_notes"] == "Looks good"


def test_reject_review_updates_checkpoint(monkeypatch, tmp_path: Path):
    """reject_review marks checkpoint as rejected with notes."""

    def fake_run_research_planning(batch, llm, **kwargs):
        return ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build auth",
            mvp_option="MVP",
            sota_option="SOTA",
            mvp_scope=["auth"],
        )

    monkeypatch.setattr("toyshop.pm.run_research_planning", fake_run_research_planning)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build auth system",
        llm=object(),
        enable_research_agent=True,
        auto_approve_research=False,
    )

    reject_review(batch.batch_dir, reviewer_notes="Need more research on auth patterns")
    cp = json.loads((batch.batch_dir / "review_checkpoint.json").read_text(encoding="utf-8"))
    assert cp["status"] == "rejected"
    assert "auth patterns" in cp["reviewer_notes"]


def test_backward_compat_auto_approve(monkeypatch, tmp_path: Path):
    """Default auto_approve_research=True preserves existing behavior (no pause)."""

    def fake_run_research_planning(batch, llm, **kwargs):
        return ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build auth",
            mvp_option="MVP",
            sota_option="SOTA",
            mvp_scope=["auth"],
        )

    def fake_run_spec_generation(batch, llm, **kwargs):
        batch.status = "in_progress"
        batch.error = None
        openspec_dir = batch.batch_dir / "openspec"
        openspec_dir.mkdir(exist_ok=True)
        for f in ("proposal.md", "design.md", "tasks.md", "spec.md"):
            (openspec_dir / f).write_text(f"# {f}\n", encoding="utf-8")
        return batch

    def fake_prepare_tasks(batch):
        return []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        batch.status = "completed"
        return _ok_tdd_result(summary=f"{mode} ok")

    monkeypatch.setattr("toyshop.pm.run_research_planning", fake_run_research_planning)
    monkeypatch.setattr("toyshop.pm.run_spec_generation", fake_run_spec_generation)
    monkeypatch.setattr("toyshop.pm.prepare_tasks", fake_prepare_tasks)
    monkeypatch.setattr("toyshop.pm.run_batch_tdd", fake_run_batch_tdd)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build auth system",
        llm=object(),
        enable_research_agent=True,
        # auto_approve_research defaults to True
    )

    assert batch.status == "completed"
    # No review checkpoint should exist (auto-approved)
    assert not (batch.batch_dir / "review_checkpoint.json").exists()


# ---------------------------------------------------------------------------
# Stage 5: Auto git binding + health check helpers
# ---------------------------------------------------------------------------

from toyshop.pm import _try_bind_git_commit, _try_health_check


def test_try_bind_git_commit_in_git_repo(tmp_path):
    """_try_bind_git_commit binds HEAD when workspace is a git repo."""
    import subprocess
    from toyshop.storage.database import init_database, create_project
    from toyshop.storage.wiki import create_version, get_version, save_test_suite
    from toyshop.storage.database import save_architecture_from_design

    db_path = tmp_path / "test.db"
    init_database(db_path)

    proj = create_project("git-test", str(tmp_path))
    snap = save_architecture_from_design(proj["id"], [], [])
    version = create_version(proj["id"], snap["id"], "create", "test")

    # Init a git repo in tmp_path
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "dummy.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

    _try_bind_git_commit(version, tmp_path)

    fetched = get_version(version.id)
    assert fetched.git_commit_hash is not None
    assert len(fetched.git_commit_hash) == 40

    from toyshop.storage.database import close_database
    close_database()


def test_try_bind_git_commit_no_git(tmp_path):
    """_try_bind_git_commit silently skips when not a git repo."""
    from types import SimpleNamespace
    version = SimpleNamespace(id="fake-id")
    # Should not raise
    _try_bind_git_commit(version, tmp_path)


def test_try_health_check_standard_level(tmp_path):
    """_try_health_check saves health check for standard management level."""
    from toyshop.storage.database import init_database, create_project, close_database
    from toyshop.storage.database import save_architecture_from_design, get_health_history
    from toyshop.storage.wiki import create_version

    db_path = tmp_path / "test.db"
    init_database(db_path)

    proj = create_project("health-test", str(tmp_path))
    snap = save_architecture_from_design(proj["id"], [], [])
    version = create_version(proj["id"], snap["id"], "create", "test")

    db_modules = [
        {"id": "m1", "name": "core", "responsibilities": ["a", "b", "c", "d", "e", "f", "g"], "dependencies": []},
    ]
    _try_health_check(version, proj["id"], "python", db_modules)

    history = get_health_history(proj["id"])
    assert len(history) == 1
    assert history[0]["warning_count"] >= 1  # bloated module warning

    close_database()


def test_try_health_check_minimal_skips(tmp_path):
    """_try_health_check skips for minimal management level."""
    from toyshop.storage.database import init_database, create_project, close_database
    from toyshop.storage.database import save_architecture_from_design, get_health_history
    from toyshop.storage.wiki import create_version

    db_path = tmp_path / "test.db"
    init_database(db_path)

    proj = create_project("minimal-test", str(tmp_path))
    snap = save_architecture_from_design(proj["id"], [], [])
    version = create_version(proj["id"], snap["id"], "create", "test")

    db_modules = [
        {"id": "m1", "name": "core", "responsibilities": ["a", "b", "c", "d", "e", "f", "g"], "dependencies": []},
    ]
    _try_health_check(version, proj["id"], "json-minecraft", db_modules)

    history = get_health_history(proj["id"])
    assert len(history) == 0  # Skipped due to minimal level

    close_database()
