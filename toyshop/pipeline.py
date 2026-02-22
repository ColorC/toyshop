"""Development pipeline orchestrator.

Runs the complete development workflow:
Requirement → Architecture → Persistence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from toyshop.llm import LLM, create_llm
from toyshop.workflows.requirement import (
    run_requirement_workflow,
    RequirementState,
)
from toyshop.workflows.architecture import (
    run_architecture_workflow,
    ArchitectureState,
)
from toyshop.storage.database import (
    init_database,
    close_database,
    create_project,
    save_architecture_from_design,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Pipeline State
# ---------------------------------------------------------------------------


@dataclass
class DevelopmentPipelineState:
    """State for the complete development pipeline."""

    # Input
    user_input: str
    project_name: str
    workspace_dir: str

    # Stage outputs
    requirement: RequirementState | None = None
    architecture: ArchitectureState | None = None

    # Persistence
    project_id: str | None = None
    snapshot_id: str | None = None

    # Control
    current_stage: str = "requirement"
    error: str | None = None


# ---------------------------------------------------------------------------
# Pipeline Runner
# ---------------------------------------------------------------------------


def run_development_pipeline(
    user_input: str,
    project_name: str,
    workspace_dir: str,
    llm: LLM | None = None,
) -> DevelopmentPipelineState:
    """Run the complete development pipeline.

    Args:
        user_input: User's project description
        project_name: Name of the project
        workspace_dir: Directory to create project files
        llm: LLM instance (created from config if not provided)

    Returns:
        Final pipeline state
    """
    # Create LLM if not provided
    if llm is None:
        llm = create_llm()

    # Ensure workspace exists
    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "openspec").mkdir(exist_ok=True)
    (ws / ".toyshop").mkdir(exist_ok=True)

    state = DevelopmentPipelineState(
        user_input=user_input,
        project_name=project_name,
        workspace_dir=workspace_dir,
    )

    # Stage 1: Requirement
    print(f"[Pipeline] Stage 1: Requirement for '{project_name}'")
    state.requirement = run_requirement_workflow(
        llm=llm,
        user_input=user_input,
        project_name=project_name,
    )

    if state.requirement.error or state.requirement.current_step != "done":
        state.error = f"Requirement stage failed: {state.requirement.error}"
        state.current_stage = "requirement"
        return state

    # Save proposal
    if state.requirement.proposal_markdown:
        (ws / "openspec" / "proposal.md").write_text(state.requirement.proposal_markdown)

    state.current_stage = "architecture"

    # Stage 2: Architecture
    print("[Pipeline] Stage 2: Architecture Design")
    state.architecture = run_architecture_workflow(
        llm=llm,
        proposal=state.requirement.proposal,
    )

    if state.architecture.error or state.architecture.current_step != "done":
        state.error = f"Architecture stage failed: {state.architecture.error}"
        return state

    # Save architecture docs
    if state.architecture.design_markdown:
        (ws / "openspec" / "design.md").write_text(state.architecture.design_markdown)
    if state.architecture.tasks_markdown:
        (ws / "openspec" / "tasks.md").write_text(state.architecture.tasks_markdown)
    if state.architecture.spec_markdown:
        (ws / "openspec" / "specs").mkdir(exist_ok=True)
        (ws / "openspec" / "specs" / "main.md").write_text(state.architecture.spec_markdown)

    state.current_stage = "persistence"

    # Stage 3: Persistence
    print("[Pipeline] Stage 3: Architecture Persistence")
    try:
        db_path = ws / ".toyshop" / "architecture.db"
        init_database(db_path)

        project = create_project(name=project_name, root_path=workspace_dir)
        state.project_id = project["id"]

        if state.architecture.design:
            modules = [m.model_dump(by_alias=True) for m in state.architecture.design.modules]
            interfaces = [i.model_dump(by_alias=True) for i in state.architecture.design.interfaces]

            snapshot = save_architecture_from_design(
                project_id=project["id"],
                modules=modules,
                interfaces=interfaces,
                source="generated",
            )
            state.snapshot_id = snapshot["id"]

        close_database()
        state.current_stage = "done"
        print("[Pipeline] Pipeline completed successfully")

    except Exception as e:
        state.error = f"Persistence stage failed: {e}"
        return state

    return state
