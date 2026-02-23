from __future__ import annotations

import json
from pathlib import Path

import pytest

from toyshop.pm import run_batch_phased
from toyshop.research_agent import ResearchPlan
from toyshop.tdd_pipeline import TDDResult

pytestmark = [pytest.mark.e2e]


def _ok_tdd_result(summary: str) -> TDDResult:
    return TDDResult(
        success=True,
        whitebox_passed=True,
        blackbox_passed=True,
        summary=summary,
    )


def test_key_flow_sequence_requirement_to_sota(monkeypatch, tmp_path: Path):
    """E2E (mocked adapters): requirement -> clarification -> research -> selection -> mvp_extract -> mvp -> sota."""
    calls: list[str] = []

    def fake_run_research_planning(batch, llm, **kwargs):
        return ResearchPlan(
            trigger_type=kwargs.get("trigger_type", "kickoff_mvp_sota"),
            problem_statement="build auth system",
            mvp_option="MVP option",
            sota_option="SOTA option",
            mvp_scope=["auth", "api"],
            tradeoffs=["speed vs quality"],
            adoption_plan=["stage_1_mvp", "checkpoint_mvp_uploaded", "stage_2_sota"],
            recommended_option="mvp_first_then_sota",
            mvp_extracted_from_sota=True,
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
    assert (batch.batch_dir / "clarification.md").exists()

    events = [
        json.loads(line)["event"]
        for line in (batch.batch_dir / "stage_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    expected = [
        "requirement_received",
        "clarification_completed",
        "research_completed",
        "option_selected",
        "mvp_scope_extracted",
        "mvp_implementation_completed",
        "sota_implementation_completed",
    ]
    for e in expected:
        assert e in events
    order = [events.index(e) for e in expected]
    assert order == sorted(order)

