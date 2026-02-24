"""E2E tests for deadlock resolution scenarios (#14).

Covers: SOTA deadlock, both MVP+SOTA deadlock, retry-still-fails paths.
Complements test_pm_phased.py::test_run_batch_phased_mvp_deadlock_resolution_retry
which only covers MVP deadlock with auto_continue_sota=False.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from toyshop.pm import run_batch_phased
from toyshop.research_agent import ResearchPlan
from toyshop.tdd_pipeline import TDDResult

pytestmark = [pytest.mark.e2e]


# ── helpers ──────────────────────────────────────────────────────────────

def _ok_tdd_result(summary: str) -> TDDResult:
    return TDDResult(
        success=True,
        whitebox_passed=True,
        blackbox_passed=True,
        summary=summary,
    )


def _fail_tdd_result(summary: str) -> TDDResult:
    return TDDResult(
        success=False,
        whitebox_passed=False,
        blackbox_passed=False,
        summary=summary,
    )


def _fake_research_planning_factory(trigger_calls: list[str]):
    def fake(batch, llm, **kwargs):
        trigger = kwargs.get("trigger_type", "kickoff_mvp_sota")
        trigger_calls.append(trigger)
        return ResearchPlan(
            trigger_type=trigger,
            problem_statement="test requirement",
            mvp_option=f"{trigger} MVP",
            sota_option=f"{trigger} SOTA",
            mvp_scope=["core"],
            tradeoffs=["speed vs quality"],
            adoption_plan=["mvp", "sota"],
        )
    return fake


def _fake_spec_generation(batch, llm, **kwargs):
    batch.status = "in_progress"
    batch.error = None
    openspec_dir = batch.batch_dir / "openspec"
    openspec_dir.mkdir(exist_ok=True)
    for f in ("proposal.md", "design.md", "tasks.md", "spec.md"):
        (openspec_dir / f).write_text(f"# {f}\n", encoding="utf-8")
    return batch


def _fake_prepare_tasks(batch):
    return []


def _apply_common_patches(monkeypatch, fake_run_batch_tdd, trigger_calls):
    monkeypatch.setattr("toyshop.pm.run_research_planning", _fake_research_planning_factory(trigger_calls))
    monkeypatch.setattr("toyshop.pm.run_spec_generation", _fake_spec_generation)
    monkeypatch.setattr("toyshop.pm.prepare_tasks", _fake_prepare_tasks)
    monkeypatch.setattr("toyshop.pm.run_batch_tdd", fake_run_batch_tdd)


def _read_events(batch) -> list[dict]:
    path = batch.batch_dir / "stage_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _read_gates(batch) -> list[dict]:
    path = batch.batch_dir / "quality_gates.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ── Test 1: SOTA deadlock resolution ─────────────────────────────────────

def test_sota_deadlock_resolution_retry(monkeypatch, tmp_path: Path):
    """MVP succeeds, SOTA fails first attempt, deadlock resolution retries and succeeds."""
    calls: list[str] = []
    trigger_calls: list[str] = []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        calls.append(mode)
        # SOTA first attempt fails, retry succeeds
        if mode == "modify" and calls.count("modify") == 1:
            batch.status = "failed"
            batch.error = "sota failed first attempt"
            return _fail_tdd_result("sota failed first attempt")
        batch.status = "completed"
        batch.error = None
        return _ok_tdd_result(f"{mode} ok")

    _apply_common_patches(monkeypatch, fake_run_batch_tdd, trigger_calls)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="deadlock-sota",
        user_input="build auth system",
        llm=object(),
        auto_continue_sota=True,
        enable_research_agent=True,
    )

    assert batch.status == "completed"
    assert calls == ["create", "modify", "modify"]
    # kickoff at start, then deadlock_resolution for SOTA
    assert trigger_calls == ["kickoff_mvp_sota", "deadlock_resolution"]

    # phase_results exists (SOTA stage reached)
    pr = json.loads((batch.batch_dir / "phase_results.json").read_text(encoding="utf-8"))
    assert pr["deadlock_recovery"]["mvp"] is False
    assert pr["deadlock_recovery"]["sota"] is True
    assert pr["mvp"]["success"] is True
    assert pr["sota"]["success"] is True

    # stage events
    events = _read_events(batch)
    sota_dl_starts = [e for e in events if e["stage"] == "sota" and e["event"] == "deadlock_resolution_start"]
    sota_dl_dones = [e for e in events if e["stage"] == "sota" and e["event"] == "deadlock_resolution_done"]
    assert len(sota_dl_starts) == 1
    assert len(sota_dl_dones) == 1

    # quality gates: 1 MVP tdd (pass), 2 SOTA tdd (fail then pass)
    gates = _read_gates(batch)
    mvp_tdd = [g for g in gates if g["stage"] == "mvp" and g["gate"] == "tdd"]
    sota_tdd = [g for g in gates if g["stage"] == "sota" and g["gate"] == "tdd"]
    assert len(mvp_tdd) == 1
    assert mvp_tdd[0]["passed"] is True
    assert len(sota_tdd) == 2
    assert sota_tdd[0]["passed"] is False
    assert sota_tdd[1]["passed"] is True


# ── Test 2: Both MVP and SOTA deadlock ────────────────────────────────────

def test_both_mvp_and_sota_deadlock(monkeypatch, tmp_path: Path):
    """Both MVP and SOTA fail first attempt, deadlock resolution retries both."""
    calls: list[str] = []
    trigger_calls: list[str] = []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        calls.append(mode)
        # First attempt of each mode fails
        if mode == "create" and calls.count("create") == 1:
            batch.status = "failed"
            batch.error = "mvp failed first"
            return _fail_tdd_result("mvp failed first")
        if mode == "modify" and calls.count("modify") == 1:
            batch.status = "failed"
            batch.error = "sota failed first"
            return _fail_tdd_result("sota failed first")
        batch.status = "completed"
        batch.error = None
        return _ok_tdd_result(f"{mode} ok")

    _apply_common_patches(monkeypatch, fake_run_batch_tdd, trigger_calls)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="deadlock-both",
        user_input="build auth system",
        llm=object(),
        auto_continue_sota=True,
        enable_research_agent=True,
    )

    assert batch.status == "completed"
    assert calls == ["create", "create", "modify", "modify"]
    # kickoff + MVP deadlock + SOTA deadlock
    assert trigger_calls == ["kickoff_mvp_sota", "deadlock_resolution", "deadlock_resolution"]

    pr = json.loads((batch.batch_dir / "phase_results.json").read_text(encoding="utf-8"))
    assert pr["deadlock_recovery"]["mvp"] is True
    assert pr["deadlock_recovery"]["sota"] is True

    events = _read_events(batch)
    dl_starts = [e for e in events if e["event"] == "deadlock_resolution_start"]
    dl_dones = [e for e in events if e["event"] == "deadlock_resolution_done"]
    assert len(dl_starts) == 2
    assert len(dl_dones) == 2
    assert {e["stage"] for e in dl_starts} == {"mvp", "sota"}

    gates = _read_gates(batch)
    mvp_tdd = [g for g in gates if g["stage"] == "mvp" and g["gate"] == "tdd"]
    sota_tdd = [g for g in gates if g["stage"] == "sota" and g["gate"] == "tdd"]
    assert len(mvp_tdd) == 2
    assert mvp_tdd[0]["passed"] is False
    assert mvp_tdd[1]["passed"] is True
    assert len(sota_tdd) == 2
    assert sota_tdd[0]["passed"] is False
    assert sota_tdd[1]["passed"] is True


# ── Test 3: MVP deadlock retry still fails ────────────────────────────────

def test_mvp_deadlock_retry_still_fails(monkeypatch, tmp_path: Path):
    """MVP always fails — deadlock resolution fires but retry also fails."""
    calls: list[str] = []
    trigger_calls: list[str] = []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        calls.append(mode)
        if mode == "create":
            batch.status = "failed"
            batch.error = "mvp always fails"
            return _fail_tdd_result("mvp always fails")
        batch.status = "completed"
        batch.error = None
        return _ok_tdd_result(f"{mode} ok")

    _apply_common_patches(monkeypatch, fake_run_batch_tdd, trigger_calls)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="deadlock-mvp-fail",
        user_input="build auth system",
        llm=object(),
        auto_continue_sota=True,
        enable_research_agent=True,
    )

    assert batch.status == "failed"
    assert batch.error is not None
    assert calls == ["create", "create"]  # original + one retry
    assert trigger_calls == ["kickoff_mvp_sota", "deadlock_resolution"]

    # phase_results should NOT exist (pipeline aborted before SOTA)
    assert not (batch.batch_dir / "phase_results.json").exists()

    # exit_conditions written for failed MVP
    ec = json.loads((batch.batch_dir / "exit_conditions.json").read_text(encoding="utf-8"))
    assert ec["passed"] is False
    assert ec["current_stage"] == "mvp"

    # deadlock events still logged
    events = _read_events(batch)
    dl_starts = [e for e in events if e["event"] == "deadlock_resolution_start"]
    assert len(dl_starts) == 1
    assert dl_starts[0]["stage"] == "mvp"

    # both MVP gates failed
    gates = _read_gates(batch)
    mvp_tdd = [g for g in gates if g["stage"] == "mvp" and g["gate"] == "tdd"]
    assert len(mvp_tdd) == 2
    assert all(g["passed"] is False for g in mvp_tdd)


# ── Test 4: SOTA deadlock retry still fails ───────────────────────────────

def test_sota_deadlock_retry_still_fails(monkeypatch, tmp_path: Path):
    """MVP succeeds, SOTA always fails — deadlock resolution fires but retry also fails."""
    calls: list[str] = []
    trigger_calls: list[str] = []

    def fake_run_batch_tdd(batch, llm, mode="create"):
        calls.append(mode)
        if mode == "modify":
            batch.status = "failed"
            batch.error = "sota always fails"
            return _fail_tdd_result("sota always fails")
        batch.status = "completed"
        batch.error = None
        return _ok_tdd_result(f"{mode} ok")

    _apply_common_patches(monkeypatch, fake_run_batch_tdd, trigger_calls)

    batch = run_batch_phased(
        pm_root=tmp_path,
        project_name="deadlock-sota-fail",
        user_input="build auth system",
        llm=object(),
        auto_continue_sota=True,
        enable_research_agent=True,
    )

    assert batch.status == "failed"
    assert calls == ["create", "modify", "modify"]
    assert trigger_calls == ["kickoff_mvp_sota", "deadlock_resolution"]

    # phase_results exists (SOTA stage was reached and wrote results)
    pr = json.loads((batch.batch_dir / "phase_results.json").read_text(encoding="utf-8"))
    assert pr["mvp"]["success"] is True
    assert pr["sota"]["success"] is False
    assert pr["deadlock_recovery"]["sota"] is True

    # exit_conditions for failed SOTA
    ec = json.loads((batch.batch_dir / "exit_conditions.json").read_text(encoding="utf-8"))
    assert ec["passed"] is False
    assert ec["current_stage"] == "sota"

    # SOTA gates: both failed
    gates = _read_gates(batch)
    sota_tdd = [g for g in gates if g["stage"] == "sota" and g["gate"] == "tdd"]
    assert len(sota_tdd) == 2
    assert all(g["passed"] is False for g in sota_tdd)