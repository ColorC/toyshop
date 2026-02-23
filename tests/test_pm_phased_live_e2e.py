"""Live E2E tests for phased PM pipeline (research -> MVP -> SOTA).

This suite verifies that:
1. Research planning is integrated into real phased execution.
2. MVP and SOTA stage requirements/spec snapshots are both produced.
3. Real TDD pipeline completes in stage order: MVP(create) -> SOTA(modify).
4. Stage gates/artifacts/events/exit conditions are consistent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import litellm
import pytest

from toyshop.llm import create_llm
from toyshop.pm import run_batch_phased


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


def _read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def llm():
    """Create LLM and skip when live phased E2E is not explicitly enabled."""
    if os.getenv("TOYSHOP_RUN_LIVE_E2E", "0") != "1":
        pytest.skip("Set TOYSHOP_RUN_LIVE_E2E=1 to run live E2E tests")
    if os.getenv("TOYSHOP_RUN_PHASED_LIVE_E2E", "0") != "1":
        pytest.skip("Set TOYSHOP_RUN_PHASED_LIVE_E2E=1 to run phased live E2E test")

    _llm = create_llm(timeout=300)
    try:
        litellm.responses(
            model=_llm.model,
            input=[{"role": "user", "content": "ping"}],
            api_key=_llm.api_key.get_secret_value() if _llm.api_key else None,
            api_base=_llm.base_url,
            timeout=20,
            num_retries=0,
            max_output_tokens=8,
        )
    except Exception as e:
        pytest.skip(f"LLM service unavailable: {e}")
    return _llm


@pytest.mark.timeout(2400)
def test_run_batch_phased_live_research_mvp_sota_tdd(llm, tmp_path: Path):
    """Real LLM E2E: research planning + MVP(create) + SOTA(modify) full chain."""
    requirement = """构建一个 Python 命令行任务管理器（todo）。

基础能力：
1. 添加任务（包含标题与优先级）
2. 列出任务
3. 标记任务完成

增强能力：
4. 支持保存/加载本地 JSON 数据
5. 支持按状态筛选（all/open/done）

请优先保证可测试性和清晰模块边界。
"""

    batch = run_batch_phased(
        pm_root=tmp_path / "pm_batches",
        project_name="phased-live-todo",
        user_input=requirement,
        llm=llm,
        project_type="python",
        auto_continue_sota=True,
        enable_research_agent=True,
        research_timebox_minutes=2,
    )

    assert batch.status == "completed", batch.error
    assert not batch.error

    batch_dir = batch.batch_dir

    # Research artifacts must exist and contain MVP/SOTA structured planning.
    research_request = _read_json(batch_dir / "research" / "request.json")
    research_result = _read_json(batch_dir / "research" / "result.json")
    assert isinstance(research_request, dict)
    assert isinstance(research_result, dict)
    assert research_request["trigger_type"] == "kickoff_mvp_sota"
    assert research_result["trigger_type"] == "kickoff_mvp_sota"
    assert research_result["mvp_option"].strip()
    assert research_result["sota_option"].strip()
    assert research_result["mvp_extracted_from_sota"] is True
    assert isinstance(research_result["mvp_scope"], list)
    assert len(research_result["mvp_scope"]) > 0

    # Stage snapshots for both MVP and SOTA specs should exist.
    for stage in ("mvp", "sota"):
        stage_dir = batch_dir / "openspec_stages" / stage
        assert stage_dir.exists(), f"missing stage snapshot dir: {stage_dir}"
        for doc in ("proposal.md", "design.md", "tasks.md", "spec.md"):
            assert (stage_dir / doc).exists(), f"missing {stage}/{doc}"

    # Stage checkpoint and final phase results.
    checkpoint = _read_json(batch_dir / "stage_checkpoint.json")
    assert isinstance(checkpoint, dict)
    assert checkpoint["current_stage"] == "done"
    assert checkpoint["stage_gate_passed"] is True

    phase_results = _read_json(batch_dir / "phase_results.json")
    assert isinstance(phase_results, dict)
    assert phase_results["mvp"]["success"] is True
    assert phase_results["sota"]["success"] is True
    assert phase_results["auto_continue_sota"] is True

    # Quality gates should prove MVP=create and SOTA=modify TDD handoff.
    quality_gates = _read_json(batch_dir / "quality_gates.json")
    assert isinstance(quality_gates, list)
    mvp_tdd_gates = [
        g for g in quality_gates
        if g.get("stage") == "mvp" and g.get("gate") == "tdd" and g.get("passed") is True
    ]
    sota_tdd_gates = [
        g for g in quality_gates
        if g.get("stage") == "sota" and g.get("gate") == "tdd" and g.get("passed") is True
    ]
    assert mvp_tdd_gates, "missing passing MVP TDD gate"
    assert sota_tdd_gates, "missing passing SOTA TDD gate"
    assert any(g.get("details", {}).get("mode") == "create" for g in mvp_tdd_gates)
    assert any(g.get("details", {}).get("mode") == "modify" for g in sota_tdd_gates)

    # Exit conditions should be successful and artifacts complete.
    exit_conditions = _read_json(batch_dir / "exit_conditions.json")
    assert isinstance(exit_conditions, dict)
    assert exit_conditions["current_stage"] == "done"
    assert exit_conditions["passed"] is True
    assert all(chk.get("exists") for chk in exit_conditions.get("artifact_checks", []))

    # Stage events should include full expected progression in order.
    event_lines = (batch_dir / "stage_events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in event_lines if line.strip()]
    expected_events = [
        "requirement_received",
        "clarification_completed",
        "research_completed",
        "option_selected",
        "mvp_scope_extracted",
        "mvp_implementation_completed",
        "checkpoint_written",
        "sota_implementation_completed",
    ]
    for name in expected_events:
        assert name in events
    positions = [events.index(name) for name in expected_events]
    assert positions == sorted(positions)
