"""Self-hosting foundation — ToyShop bootstraps itself into its own Wiki.

Provides:
- bootstrap_self(): Load ToyShop's own codebase into the wiki
- record_pipeline_run(): Track pipeline executions in workflow_runs
- generate_self_change_request(): Generate change requests against ToyShop's wiki state
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from toyshop.llm import LLM


# Default ToyShop root (relative to this file)
_TOYSHOP_ROOT = Path(__file__).resolve().parent.parent


def bootstrap_self(
    db_path: str | Path | None = None,
    *,
    smart: bool = False,
    llm: "LLM | None" = None,
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
    llm: "LLM | None" = None,
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
            from toyshop.snapshot import create_snapshot
            from toyshop.impact import run_impact_analysis, save_impact

            # Build a CodeSnapshot from the stored snapshot data
            code_snapshot = create_snapshot(
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
