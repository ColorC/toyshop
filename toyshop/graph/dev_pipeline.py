"""Development pipeline graph facade (Phase 5 incremental).

Note:
- We intentionally avoid adding hard dependency on langgraph in this phase.
- This module provides a graph-compatible orchestration shell so callers can
  switch via feature flag without touching business logic.
- When langgraph dependency is added, this file can be upgraded in-place.
"""

from __future__ import annotations

from typing import Any

from toyshop.graph.state import DevGraphState


def run_dev_graph(
    state: DevGraphState,
    *,
    llm: Any,
) -> DevGraphState:
    """Run development flow through graph facade.

    Current behavior delegates to pm.run_batch to preserve correctness and
    avoid duplicate logic during migration.
    """
    from toyshop.pm import run_batch

    batch = run_batch(
        pm_root=state.pm_root,
        project_name=state.project_name,
        user_input=state.user_input,
        llm=llm,
        project_type=state.project_type,
    )

    state.batch_id = batch.batch_id
    state.batch_dir = str(batch.batch_dir)
    state.status = batch.status
    state.error = batch.error

    openspec_dir = batch.batch_dir / "openspec"
    if openspec_dir.exists():
        proposal = openspec_dir / "proposal.md"
        design = openspec_dir / "design.md"
        tasks = openspec_dir / "tasks.md"
        spec = openspec_dir / "spec.md"
        if proposal.exists():
            state.proposal_md = proposal.read_text(encoding="utf-8")
        if design.exists():
            state.design_md = design.read_text(encoding="utf-8")
        if tasks.exists():
            state.tasks_md = tasks.read_text(encoding="utf-8")
        if spec.exists():
            state.spec_md = spec.read_text(encoding="utf-8")

    return state
