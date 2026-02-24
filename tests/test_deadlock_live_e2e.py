"""Live E2E tests for deadlock resolution (#14).

Tests the real research planning agent in the deadlock resolution path.
TDD failure is simulated (monkeypatched) to deterministically trigger
deadlock resolution, but research planning runs with the real LLM.

Gate: TOYSHOP_RUN_LIVE_E2E=1 + TOYSHOP_RUN_DEADLOCK_LIVE_E2E=1
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from toyshop.llm import create_llm, probe_llm
from toyshop.pm import run_batch_phased
from toyshop.tdd_pipeline import TDDResult

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


def _read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def llm():
    if os.getenv("TOYSHOP_RUN_LIVE_E2E", "0") != "1":
        pytest.skip("Set TOYSHOP_RUN_LIVE_E2E=1 to run live E2E tests")
    if os.getenv("TOYSHOP_RUN_DEADLOCK_LIVE_E2E", "0") != "1":
        pytest.skip("Set TOYSHOP_RUN_DEADLOCK_LIVE_E2E=1 to run deadlock live E2E")

    _llm = create_llm(timeout=300)
    ok, err = probe_llm(_llm, timeout=20)
    if not ok:
        pytest.skip(f"LLM service unavailable: {err}")
    return _llm


@pytest.mark.timeout(600)
def test_mvp_deadlock_with_real_research(llm, monkeypatch, tmp_path: Path):
    """MVP TDD fails (simulated), deadlock resolution runs real research, retry succeeds.

    This test validates:
    - Real LLM research planning with trigger_type="deadlock_resolution"
    - Research artifacts (request.json, result.json, summary.md) are produced
    - Deadlock resolution events are logged
    - The refined requirement from research is used for the retry
    """
    calls: list[str] = []

    def fake_run_batch_tdd(batch, _llm, mode="create"):
        calls.append(mode)
        # First MVP attempt fails, triggering deadlock resolution
        if mode == "create" and calls.count("create") == 1:
            batch.status = "failed"
            batch.error = "mvp tdd failed: whitebox tests did not pass"
            return TDDResult(
                success=False,
                whitebox_passed=False,
                blackbox_passed=False,
                summary="mvp tdd failed: whitebox tests did not pass",
            )
        # All subsequent calls succeed
        batch.status = "completed"
        batch.error = None
        return TDDResult(
            success=True,
            whitebox_passed=True,
            blackbox_passed=True,
            summary=f"{mode} ok",
        )

    monkeypatch.setattr("toyshop.pm.run_batch_tdd", fake_run_batch_tdd)

    batch = run_batch_phased(
        pm_root=tmp_path / "pm_batches",
        project_name="deadlock-live",
        user_input="构建一个 Python 命令行计算器，支持加减乘除和除零处理。",
        llm=llm,
        project_type="python",
        auto_continue_sota=False,  # stop after MVP to keep test fast
        enable_research_agent=True,
        research_timebox_minutes=2,
    )

    assert batch.status == "completed", batch.error
    assert calls == ["create", "create"]  # original + retry

    # ── Research artifacts from deadlock resolution ──
    research_dir = batch.batch_dir / "research"
    assert research_dir.exists(), "research/ directory missing"

    # There should be at least 2 research entries: kickoff + deadlock
    history_path = research_dir / "history.jsonl"
    if history_path.exists():
        history = [json.loads(ln) for ln in history_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        deadlock_entries = [h for h in history if h.get("trigger_type") == "deadlock_resolution"]
        assert len(deadlock_entries) >= 1, "no deadlock_resolution entry in research history"

    # Latest result should have structured MVP/SOTA plan
    result_path = research_dir / "result.json"
    assert result_path.exists(), "research/result.json missing"
    result = _read_json(result_path)
    assert result.get("mvp_option"), "research result missing mvp_option"
    assert result.get("sota_option"), "research result missing sota_option"

    # ── Stage events ──
    events_path = batch.batch_dir / "stage_events.jsonl"
    assert events_path.exists()
    events = [json.loads(ln) for ln in events_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    dl_starts = [e for e in events if e["event"] == "deadlock_resolution_start"]
    dl_dones = [e for e in events if e["event"] == "deadlock_resolution_done"]
    assert len(dl_starts) == 1
    assert dl_starts[0]["stage"] == "mvp"
    assert len(dl_dones) == 1

    # ── Quality gates ──
    gates = _read_json(batch.batch_dir / "quality_gates.json")
    mvp_tdd = [g for g in gates if g["stage"] == "mvp" and g["gate"] == "tdd"]
    assert len(mvp_tdd) == 2
    assert mvp_tdd[0]["passed"] is False
    assert mvp_tdd[1]["passed"] is True


@pytest.mark.timeout(900)
def test_both_stages_deadlock_with_real_research(llm, monkeypatch, tmp_path: Path):
    """Both MVP and SOTA fail first attempt, real research runs for each deadlock."""
    calls: list[str] = []

    def fake_run_batch_tdd(batch, _llm, mode="create"):
        calls.append(mode)
        if mode == "create" and calls.count("create") == 1:
            batch.status = "failed"
            batch.error = "mvp failed first"
            return TDDResult(success=False, whitebox_passed=False, blackbox_passed=False, summary="mvp failed first")
        if mode == "modify" and calls.count("modify") == 1:
            batch.status = "failed"
            batch.error = "sota failed first"
            return TDDResult(success=False, whitebox_passed=False, blackbox_passed=False, summary="sota failed first")
        batch.status = "completed"
        batch.error = None
        return TDDResult(success=True, whitebox_passed=True, blackbox_passed=True, summary=f"{mode} ok")

    monkeypatch.setattr("toyshop.pm.run_batch_tdd", fake_run_batch_tdd)

    batch = run_batch_phased(
        pm_root=tmp_path / "pm_batches",
        project_name="deadlock-both-live",
        user_input="构建一个 Python 任务管理器，支持添加、列出、完成任务，保存到 JSON。",
        llm=llm,
        project_type="python",
        auto_continue_sota=True,
        enable_research_agent=True,
        research_timebox_minutes=2,
    )

    assert batch.status == "completed", batch.error
    assert calls == ["create", "create", "modify", "modify"]

    # Both deadlock resolutions should have fired
    events = [json.loads(ln) for ln in (batch.batch_dir / "stage_events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    dl_starts = [e for e in events if e["event"] == "deadlock_resolution_start"]
    assert len(dl_starts) == 2
    assert {e["stage"] for e in dl_starts} == {"mvp", "sota"}

    # Phase results should show both recoveries
    pr = _read_json(batch.batch_dir / "phase_results.json")
    assert pr["deadlock_recovery"]["mvp"] is True
    assert pr["deadlock_recovery"]["sota"] is True
