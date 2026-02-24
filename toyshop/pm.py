"""PM System — File-based project management with end-to-end pipeline.

Greenfield workflow:
  1. create_batch()        — create batch folder, save requirements.md
  2. run_spec_generation()  — requirement + architecture workflows → openspec docs
  3. prepare_tasks()        — parse tasks.md → create task folders (display only)
  4. run_batch_tdd()        — run single TDD pipeline for entire batch
  5. run_batch()            — orchestrate 1-4 serially
  6. resume_batch()         — resume if TDD not yet completed

Change (brownfield) workflow:
  1. create_change_batch()  — create batch from existing workspace + change request
  2. run_change_analysis()  — snapshot code + LLM impact analysis
  3. run_spec_evolution()   — update openspec docs based on impact
  4. run_batch_tdd(mode="modify") — incremental TDD pipeline
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from toyshop.llm import LLM, create_llm
from toyshop.workflows.requirement import run_requirement_workflow
from toyshop.workflows.architecture import run_architecture_workflow
from toyshop.tdd_pipeline import run_tdd_pipeline, TDDResult
from toyshop.snapshot import create_snapshot, save_snapshot, CodeSnapshot
from toyshop.impact import (
    run_impact_analysis, save_impact, load_impact, ImpactAnalysis,
    check_architecture_health,
)
from toyshop.spec_evolution import (
    evolve_proposal, evolve_design, evolve_tasks, evolve_spec,
    verify_evolution,
)
from toyshop.research_agent import (
    ResearchPlan,
    generate_kickoff_plan,
    default_research_plan,
)

ALLOWED_RESEARCH_TRIGGERS = {"kickoff_mvp_sota", "deadlock_resolution"}


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class TaskState:
    id: str
    title: str
    description: str
    status: str = "pending"  # pending | in_progress | completed | failed | skipped
    dependencies: list[str] = field(default_factory=list)
    assigned_module: str | None = None
    task_dir: Path | None = None


@dataclass
class BatchState:
    batch_id: str
    project_name: str
    batch_dir: Path
    status: str = "pending"  # pending | in_progress | completed | failed
    tasks: list[TaskState] = field(default_factory=list)
    error: str | None = None
    project_type: str = "python"  # "python" | "java" | "java-minecraft" | "json-minecraft"


@dataclass
class ReviewCheckpoint:
    """Human review checkpoint — pipeline pauses here until approved."""

    checkpoint_id: str
    checkpoint_type: str  # "research_review" | "mvp_review"
    batch_id: str
    artifacts_to_review: list[str]
    status: str = "pending"  # pending | approved | rejected | skipped
    reviewer_notes: str = ""
    created_at: str = ""


# =============================================================================
# Helpers
# =============================================================================
def _slugify(text: str, max_len: int = 30) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_progress(batch: BatchState) -> None:
    """Write progress.json for a batch."""
    completed = sum(1 for t in batch.tasks if t.status == "completed")
    failed = sum(1 for t in batch.tasks if t.status == "failed")
    current = next((t.id for t in batch.tasks if t.status == "in_progress"), None)
    _write_json(batch.batch_dir / "progress.json", {
        "batch_id": batch.batch_id,
        "project_name": batch.project_name,
        "project_type": batch.project_type,
        "status": batch.status,
        "total_tasks": len(batch.tasks),
        "completed_tasks": completed,
        "failed_tasks": failed,
        "current_task": current,
        "user_notes": "",
    })


def _save_task_json(task: TaskState) -> None:
    """Write task.json for a single task."""
    if task.task_dir is None:
        return
    data = {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "dependencies": task.dependencies,
        "assigned_module": task.assigned_module,
    }
    _write_json(task.task_dir / "task.json", data)


def _append_stage_event(
    batch: BatchState,
    *,
    stage: str,
    event: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Append a stage event line for phased pipeline observability."""
    event_path = batch.batch_dir / "stage_events.jsonl"
    payload = {
        "timestamp": _now_iso(),
        "stage": stage,
        "event": event,
        "details": details or {},
    }
    with event_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_stage_checkpoint(
    batch: BatchState,
    *,
    current_stage: str,
    gate_passed: bool,
    artifact_refs: list[str] | None = None,
) -> None:
    """Write stage checkpoint state (MVP/SOTA progression)."""
    checkpoint = {
        "batch_id": batch.batch_id,
        "current_stage": current_stage,  # mvp | mvp_uploaded | sota | done
        "stage_gate_passed": gate_passed,
        "stage_artifact_refs": artifact_refs or [],
        "updated_at": _now_iso(),
    }
    _write_json(batch.batch_dir / "stage_checkpoint.json", checkpoint)


def _write_clarification_artifact(batch: BatchState, clarified_requirement: str) -> None:
    """Persist requirement clarification artifact for phased traceability."""
    lines = [
        "# Clarification",
        "",
        "## Clarified Requirement",
        clarified_requirement.strip(),
        "",
        f"_updated_at: {_now_iso()}_",
    ]
    (batch.batch_dir / "clarification.md").write_text("\n".join(lines), encoding="utf-8")


def _append_quality_gate(
    batch: BatchState,
    *,
    stage: str,
    gate: str,
    passed: bool,
    details: dict[str, Any] | None = None,
) -> None:
    """Append a quality gate result for phased observability and auditing."""
    gates_path = batch.batch_dir / "quality_gates.json"
    gates: list[dict[str, Any]] = []
    if gates_path.exists():
        prev = _read_json(gates_path)
        if isinstance(prev, list):
            gates = prev

    gates.append({
        "timestamp": _now_iso(),
        "stage": stage,
        "gate": gate,
        "passed": passed,
        "details": details or {},
    })
    _write_json(gates_path, gates)


def _write_exit_conditions(
    batch: BatchState,
    *,
    current_stage: str,
    passed: bool,
    reasons: list[str] | None = None,
    required_artifacts: list[str] | None = None,
) -> None:
    """Persist exit condition checks for phased completion/failure paths."""
    required = required_artifacts or []
    artifact_checks = []
    for rel_path in required:
        artifact_checks.append({
            "path": rel_path,
            "exists": (batch.batch_dir / rel_path).exists(),
        })

    payload = {
        "run_id": batch.batch_id,
        "current_stage": current_stage,
        "passed": passed,
        "status": batch.status,
        "reasons": reasons or [],
        "artifact_checks": artifact_checks,
        "updated_at": _now_iso(),
    }
    _write_json(batch.batch_dir / "exit_conditions.json", payload)


def _save_research_artifacts(
    batch: BatchState,
    plan: ResearchPlan,
    *,
    trigger_type: str,
    timebox_minutes: int,
    enable_external_research: bool,
    problem_statement: str | None = None,
    local_attempt_summary: str = "",
) -> None:
    """Persist research request/result artifacts for audit and replay."""
    research_dir = batch.batch_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    request_problem = problem_statement
    if request_problem is None:
        request_problem = (batch.batch_dir / "requirements.md").read_text(encoding="utf-8")

    request_payload = {
        "trigger_type": trigger_type,
        "problem_statement": request_problem,
        "local_attempt_summary": local_attempt_summary,
        "constraints": {
            "project_type": batch.project_type,
            "timebox_minutes": timebox_minutes,
            "enable_external_research": enable_external_research,
        },
        "created_at": _now_iso(),
    }
    artifact_prefix = f"{_now_compact()}_{trigger_type}"
    result_payload = plan.to_dict()

    # Keep legacy paths for compatibility (latest request/result)
    _write_json(research_dir / "request.json", request_payload)
    _write_json(research_dir / "result.json", result_payload)
    # Append-only artifacts for replay/audit
    _write_json(research_dir / f"{artifact_prefix}_request.json", request_payload)
    _write_json(research_dir / f"{artifact_prefix}_result.json", result_payload)

    lines = [
        "# Research Plan",
        "",
        f"- trigger_type: `{plan.trigger_type}`",
        f"- recommended_option: `{plan.recommended_option}`",
        f"- generated_at: `{request_payload['created_at']}`",
        "",
        "## MVP Option",
        plan.mvp_option,
        "",
        "## SOTA Option",
        plan.sota_option,
        "",
        "## MVP Scope",
    ]
    for item in plan.mvp_scope:
        lines.append(f"- {item}")
    lines.extend(["", "## Tradeoffs"])
    for item in plan.tradeoffs:
        lines.append(f"- {item}")
    lines.extend(["", "## Adoption Plan"])
    for item in plan.adoption_plan:
        lines.append(f"- {item}")
    if plan.external_summary:
        lines.extend(["", "## External Summary", plan.external_summary])
    if plan.sources:
        lines.extend(["", "## Sources"])
        for s in plan.sources:
            lines.append(f"- {s}")

    summary_text = "\n".join(lines)
    (research_dir / "summary.md").write_text(summary_text, encoding="utf-8")
    (research_dir / f"{artifact_prefix}_summary.md").write_text(summary_text, encoding="utf-8")

    history_path = research_dir / "history.jsonl"
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": request_payload["created_at"],
            "trigger_type": trigger_type,
            "artifact_prefix": artifact_prefix,
            "sources_count": len(plan.sources),
            "has_external_summary": bool(plan.external_summary),
        }, ensure_ascii=False) + "\n")


# =============================================================================
# Review checkpoints
# =============================================================================

def _write_review_checkpoint(
    batch: BatchState,
    checkpoint_type: str,
    artifacts: list[str],
) -> ReviewCheckpoint:
    """Write a review checkpoint file. Pipeline pauses here until approved."""
    import uuid
    cp = ReviewCheckpoint(
        checkpoint_id=str(uuid.uuid4())[:8],
        checkpoint_type=checkpoint_type,
        batch_id=batch.batch_id,
        artifacts_to_review=artifacts,
        status="pending",
        created_at=_now_iso(),
    )
    _write_json(batch.batch_dir / "review_checkpoint.json", asdict(cp))
    return cp


def _check_review_checkpoint(batch: BatchState) -> ReviewCheckpoint | None:
    """Read the review checkpoint file if it exists."""
    path = batch.batch_dir / "review_checkpoint.json"
    if not path.exists():
        return None
    data = _read_json(path)
    return ReviewCheckpoint(**data)


def approve_review(batch_dir: Path, reviewer_notes: str = "") -> None:
    """Mark the review checkpoint as approved (CLI-callable)."""
    path = Path(batch_dir) / "review_checkpoint.json"
    if not path.exists():
        raise FileNotFoundError(f"No review checkpoint at {path}")
    data = _read_json(path)
    data["status"] = "approved"
    data["reviewer_notes"] = reviewer_notes
    _write_json(path, data)


def reject_review(batch_dir: Path, reviewer_notes: str) -> None:
    """Mark the review checkpoint as rejected with feedback (CLI-callable)."""
    path = Path(batch_dir) / "review_checkpoint.json"
    if not path.exists():
        raise FileNotFoundError(f"No review checkpoint at {path}")
    data = _read_json(path)
    data["status"] = "rejected"
    data["reviewer_notes"] = reviewer_notes
    _write_json(path, data)


def _build_stage_requirement(user_input: str, plan: ResearchPlan, stage: str) -> str:
    """Build stage-targeted requirement text from research plan."""
    if stage == "mvp":
        scope_lines = "\n".join(f"- {s}" for s in plan.mvp_scope) if plan.mvp_scope else "- core happy path"
        return (
            f"{user_input.strip()}\n\n"
            "## Stage Target: MVP\n"
            f"{plan.mvp_option}\n\n"
            "## MVP Scope\n"
            f"{scope_lines}\n\n"
            "## Execution Rule\n"
            "Focus on minimal verifiable implementation and tests."
        )
    if stage == "sota":
        tradeoff_lines = "\n".join(f"- {t}" for t in plan.tradeoffs) if plan.tradeoffs else "- n/a"
        return (
            f"{user_input.strip()}\n\n"
            "## Stage Target: SOTA\n"
            f"{plan.sota_option}\n\n"
            "## Baseline\n"
            "MVP has been completed. Improve quality, robustness, and best practices.\n\n"
            "## Tradeoffs\n"
            f"{tradeoff_lines}\n"
        )
    return user_input.strip()


def _build_structured_stage_requirement(
    user_input: str,
    spec: "ResearchSpec",
    stage: str,
) -> str:
    """Build anchored, structured stage requirement from ResearchSpec.

    The original requirement always appears first with an explicit
    "do not deviate" marker, preventing research results from dominating.
    """
    from toyshop.research_agent import ResearchSpec  # noqa: F811

    lines = [
        "## 原始需求（不可偏离）",
        spec.original_requirement,
        "",
    ]

    if stage == "mvp":
        lines.extend([
            "## 阶段目标: MVP",
            "实现最小端到端可验证路径。",
            "",
            "## MVP 范围",
        ])
        for b in spec.mvp_boundaries:
            lines.append(f"- {b}")
        lines.extend(["", "## 验收标准"])
        for a in spec.acceptance_criteria:
            lines.append(f"- {a}")
        lines.extend(["", "## 本阶段范围外（不要实现）"])
        for d in spec.deferred_to_sota:
            lines.append(f"- {d}")
    elif stage == "sota":
        lines.extend([
            "## 阶段目标: SOTA",
            "在 MVP 基础上提升到 SOTA 标准。",
            "",
            "## SOTA 标准",
        ])
        for c in spec.sota_criteria:
            lines.append(f"- {c}")
        lines.extend(["", "## 基线"])
        lines.append("MVP 已完成，在此基础上增强质量、健壮性和最佳实践。")

    if spec.architecture_constraints:
        lines.extend(["", "## 架构约束"])
        for c in spec.architecture_constraints:
            lines.append(f"- {c}")

    if spec.risk_items:
        lines.extend(["", "## 风险项"])
        for r in spec.risk_items:
            lines.append(f"- {r}")

    return "\n".join(lines)


# =============================================================================
# Task parsing
# =============================================================================

def parse_tasks_md(text: str) -> list[dict[str, Any]]:
    """Parse tasks.md into a list of task dicts.

    Supports multiple formats generated by the architecture workflow:

    Format A (openspec_bridge._tasks_to_markdown):
      ## 1. Top-level Title
      ### 1.1 Subtask Title
      **Dependencies:** 1.0, 1.1
      **Module:** parser

    Format B (render_tasks_markdown with emoji):
      ## ⬜ 1. Top-level Title
      - ⬜ **1.1** Subtask Title
        - Module: `parser`
        - Dependencies: 1.0, 1.1
    """
    tasks: list[dict[str, Any]] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Format A: ## X. Title  or  ## ⬜ X. Title
        m_top = re.match(r"^##\s+(?:⬜\s+)?(\d+)\.\s+(.+)", line)
        # Format A: ### X.Y Title
        m_sub_h3 = re.match(r"^###\s+([\d.]+)\s+(.+)", line)
        # Format B: - ⬜ **X.Y** Title
        m_sub_bullet = re.match(r"^-\s+⬜\s+\*\*([\d.]+)\*\*\s+(.+)", line)

        # Format C: simple numbered list "1. Title"
        m_simple = re.match(r"^(\d+)\.\s+(.+)", line) if not m_top and not m_sub_h3 and not m_sub_bullet else None

        if m_top:
            task_id = m_top.group(1)
            title = m_top.group(2).strip()
            # Collect description until next heading or subtask
            desc_lines: list[str] = []
            i += 1
            while i < len(lines):
                ln = lines[i]
                if ln.startswith("## ") or ln.startswith("### ") or re.match(r"^-\s+⬜\s+\*\*", ln):
                    break
                if ln.strip() and not ln.strip().startswith("Format:"):
                    desc_lines.append(ln.strip())
                i += 1
            tasks.append({
                "id": task_id,
                "title": title,
                "description": "\n".join(desc_lines),
                "dependencies": [],
                "assigned_module": None,
            })

        elif m_sub_h3 or m_sub_bullet:
            m = m_sub_h3 or m_sub_bullet
            task_id = m.group(1)
            title = m.group(2).strip()
            desc_lines = []
            deps: list[str] = []
            module: str | None = None
            i += 1
            while i < len(lines):
                ln = lines[i]
                # Stop at next heading or next subtask bullet
                if ln.startswith("## ") or ln.startswith("### "):
                    break
                if re.match(r"^-\s+⬜\s+\*\*", ln):
                    break
                # Format A metadata
                dep_m = re.match(r"\s*\*\*Dependencies:\*\*\s*(.+)", ln)
                mod_m = re.match(r"\s*\*\*Module:\*\*\s*(.+)", ln)
                # Format B metadata: "  - Module: `name`"
                mod_b = re.match(r"\s+-\s+Module:\s*`?([^`]+)`?", ln)
                dep_b = re.match(r"\s+-\s+Dependencies:\s*(.+)", ln)
                if dep_m:
                    deps = [d.strip() for d in dep_m.group(1).split(",") if d.strip()]
                elif dep_b:
                    deps = [d.strip() for d in dep_b.group(1).split(",") if d.strip()]
                elif mod_m:
                    module = mod_m.group(1).strip().strip("`")
                elif mod_b:
                    module = mod_b.group(1).strip()
                elif ln.strip():
                    desc_lines.append(ln.strip())
                i += 1
            tasks.append({
                "id": task_id,
                "title": title,
                "description": "\n".join(desc_lines),
                "dependencies": deps,
                "assigned_module": module,
            })
        elif m_simple:
            task_id = m_simple.group(1)
            title = m_simple.group(2).strip()
            tasks.append({
                "id": task_id,
                "title": title,
                "description": "",
                "dependencies": [],
                "assigned_module": None,
            })
            i += 1
        else:
            i += 1

    return tasks


# =============================================================================
# Core functions
# =============================================================================

def run_research_planning(
    batch: BatchState,
    llm: LLM,
    *,
    trigger_type: str = "kickoff_mvp_sota",
    timebox_minutes: int = 8,
    enable_external_research: bool = True,
    problem_statement: str | None = None,
    local_attempt_summary: str = "",
) -> ResearchPlan:
    """Generate research-backed MVP/SOTA plan and persist artifacts."""
    if trigger_type not in ALLOWED_RESEARCH_TRIGGERS:
        raise ValueError(f"Unsupported trigger_type: {trigger_type}")

    requirements = (batch.batch_dir / "requirements.md").read_text(encoding="utf-8")
    query_text = problem_statement if problem_statement is not None else requirements
    _append_stage_event(
        batch,
        stage="research",
        event="planning_start",
        details={
            "trigger_type": trigger_type,
            "timebox_minutes": timebox_minutes,
            "enable_external_research": enable_external_research,
            "has_local_attempt_summary": bool(local_attempt_summary),
        },
    )
    try:
        plan = generate_kickoff_plan(
            user_input=query_text,
            llm=llm,
            trigger_type=trigger_type,
            enable_external_research=enable_external_research,
            timebox_minutes=timebox_minutes,
        )
    except Exception:
        plan = default_research_plan(query_text, trigger_type=trigger_type)

    _save_research_artifacts(
        batch,
        plan,
        trigger_type=trigger_type,
        timebox_minutes=timebox_minutes,
        enable_external_research=enable_external_research,
        problem_statement=query_text,
        local_attempt_summary=local_attempt_summary,
    )
    _append_stage_event(
        batch,
        stage="research",
        event="planning_done",
        details={"recommended_option": plan.recommended_option, "sources": len(plan.sources)},
    )
    return plan


def create_batch(
    pm_root: str | Path,
    project_name: str,
    user_input: str,
    project_type: str = "python",
) -> BatchState:
    """Create a new batch folder with requirements.md."""
    pm_root = Path(pm_root)
    pm_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"{timestamp}_{_slugify(project_name)}"
    batch_dir = pm_root / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    # Save raw requirements
    (batch_dir / "requirements.md").write_text(
        f"# Requirements: {project_name}\n\n{user_input}\n",
        encoding="utf-8",
    )

    batch = BatchState(
        batch_id=batch_id,
        project_name=project_name,
        batch_dir=batch_dir,
        status="pending",
        project_type=project_type,
    )
    _save_progress(batch)
    print(f"[PM] Created batch: {batch_dir}")
    return batch


def run_spec_generation(
    batch: BatchState,
    llm: LLM,
    *,
    user_input_override: str | None = None,
    stage_name: str | None = None,
) -> BatchState:
    """Run requirement + architecture workflows, save openspec docs."""
    print("[PM] Running spec generation (requirement → architecture)")
    batch.status = "in_progress"
    _save_progress(batch)

    openspec_dir = batch.batch_dir / "openspec"
    openspec_dir.mkdir(exist_ok=True)
    user_input_text = user_input_override or (batch.batch_dir / "requirements.md").read_text(encoding="utf-8")

    # Requirement workflow
    req_state = run_requirement_workflow(
        llm=llm,
        user_input=user_input_text,
        project_name=batch.project_name,
    )
    if req_state.error or req_state.current_step != "done":
        batch.status = "failed"
        batch.error = f"Requirement workflow failed: {req_state.error}"
        _save_progress(batch)
        return batch

    if req_state.proposal_markdown:
        (openspec_dir / "proposal.md").write_text(req_state.proposal_markdown, encoding="utf-8")
        print(f"  Saved proposal.md")

    # Architecture workflow
    arch_state = run_architecture_workflow(llm=llm, proposal=req_state.proposal)
    if arch_state.error or arch_state.current_step != "done":
        batch.status = "failed"
        batch.error = f"Architecture workflow failed: {arch_state.error}"
        _save_progress(batch)
        return batch

    if arch_state.design_markdown:
        (openspec_dir / "design.md").write_text(arch_state.design_markdown, encoding="utf-8")
        print(f"  Saved design.md")
    if arch_state.tasks_markdown:
        (openspec_dir / "tasks.md").write_text(arch_state.tasks_markdown, encoding="utf-8")
        print(f"  Saved tasks.md")
    if arch_state.spec_markdown:
        (openspec_dir / "spec.md").write_text(arch_state.spec_markdown, encoding="utf-8")
        print(f"  Saved spec.md")

    # Keep stage snapshots for phased execution replay.
    if stage_name:
        stage_dir = batch.batch_dir / "openspec_stages" / stage_name
        stage_dir.mkdir(parents=True, exist_ok=True)
        for name in ["proposal.md", "design.md", "tasks.md", "spec.md"]:
            src = openspec_dir / name
            if src.exists():
                (stage_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    _save_progress(batch)
    return batch


def prepare_tasks(batch: BatchState) -> list[TaskState]:
    """Parse tasks.md and create task folders."""
    tasks_md_path = batch.batch_dir / "openspec" / "tasks.md"
    if not tasks_md_path.exists():
        print("[PM] No tasks.md found")
        return []

    raw_tasks = parse_tasks_md(tasks_md_path.read_text(encoding="utf-8"))
    print(f"[PM] Parsed {len(raw_tasks)} tasks from tasks.md")

    tasks_root = batch.batch_dir / "tasks"
    tasks_root.mkdir(exist_ok=True)

    task_states: list[TaskState] = []
    for t in raw_tasks:
        slug = _slugify(t["title"])
        task_dir = tasks_root / f"{t['id']}_{slug}"
        task_dir.mkdir(exist_ok=True)

        ts = TaskState(
            id=t["id"],
            title=t["title"],
            description=t["description"],
            dependencies=t.get("dependencies", []),
            assigned_module=t.get("assigned_module"),
            task_dir=task_dir,
        )
        _save_task_json(ts)
        task_states.append(ts)

    batch.tasks = task_states
    _save_progress(batch)
    return task_states


def _try_bind_git_commit(version, workspace: Path) -> None:
    """Try to detect the current git HEAD and bind it to the wiki version."""
    import subprocess
    from toyshop.storage.wiki import bind_git_commit

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            commit_hash = result.stdout.strip()
            if commit_hash:
                bind_git_commit(version.id, commit_hash)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # Not a git repo or git not available — skip silently


def _try_health_check(
    version, project_id: str, project_type: str, db_modules: list[dict],
) -> None:
    """Run architecture health check and save results if management_level allows."""
    from toyshop.project_type import get_project_type
    from toyshop.impact import check_architecture_health
    from toyshop.storage.database import save_health_check

    try:
        pt = get_project_type(project_type)
    except KeyError:
        pt = None

    if pt and pt.management_level == "minimal":
        return  # Skip health checks for minimal projects

    if not db_modules:
        return  # Nothing to check

    # Build a lightweight adapter object for check_architecture_health
    class _Mod:
        def __init__(self, d: dict):
            self.id = d.get("id", "")
            self.name = d.get("name", "")
            self.responsibilities = d.get("responsibilities", [])
            self.dependencies = d.get("dependencies", [])

    class _Design:
        def __init__(self, modules: list[dict]):
            self.modules = [_Mod(m) for m in modules]
            self.interfaces = []  # Interfaces checked separately

    design = _Design(db_modules)
    warnings = check_architecture_health(design)
    save_health_check(version.id, project_id, warnings)


def _create_wiki_version(
    batch: BatchState, workspace: Path, result: TDDResult, mode: str,
) -> None:
    """Create a wiki version + test suite after successful TDD."""
    import re as _re
    from toyshop.storage.database import (
        init_database, get_latest_snapshot, create_project, get_db,
        save_architecture_from_design,
    )
    from toyshop.storage.wiki import (
        create_version, save_test_suite, extract_test_metadata, log_event,
    )
    from toyshop.tdd_pipeline import _parse_design_modules, _parse_design_interfaces

    db_path = workspace / ".toyshop" / "architecture.db"
    init_database(db_path)

    # Find or create project
    db = get_db()
    cur = db.execute(
        "SELECT id FROM projects WHERE name = ? LIMIT 1",
        (batch.project_name,),
    )
    row = cur.fetchone()
    if row:
        project_id = row["id"]
    else:
        proj = create_project(batch.project_name, str(workspace))
        project_id = proj["id"]

    # Parse design.md → structured snapshot of modules + interfaces
    snapshot_id = None
    db_modules: list[dict] = []
    db_interfaces: list[dict] = []
    design_path = batch.batch_dir / "openspec" / "design.md"
    if design_path.exists():
        design_text = design_path.read_text(encoding="utf-8")
        modules = _parse_design_modules(design_text)
        interfaces = _parse_design_interfaces(design_text)
        # Convert to DB format (add moduleId linkage)
        mod_name_to_id: dict[str, str] = {}
        for m in modules:
            import uuid as _uuid
            mid = str(_uuid.uuid4())[:8]
            mod_name_to_id[m.get("name", "")] = mid
            db_modules.append({
                "id": mid,
                "name": m.get("name", ""),
                "filePath": m.get("filePath", ""),
                "responsibilities": m.get("responsibilities", []),
                "dependencies": m.get("dependencies", []),
            })
        for iface in interfaces:
            imod = iface.get("module", "")
            module_id = mod_name_to_id.get(imod, "")
            db_interfaces.append({
                "id": str(_uuid.uuid4())[:8],
                "moduleId": module_id,
                "name": iface.get("name", ""),
                "type": "class" if iface.get("signature", "").startswith("class ") else "function",
                "signature": iface.get("signature", ""),
            })
        if db_modules or db_interfaces:
            snap = save_architecture_from_design(
                project_id, db_modules, db_interfaces, source="tdd_pipeline",
            )
            snapshot_id = snap["id"]

    if snapshot_id is None:
        latest_snap = get_latest_snapshot(project_id)
        snapshot_id = latest_snap["id"] if latest_snap else None

    # Parse pass/fail from whitebox_output summary line
    passed = failed = 0
    m = _re.search(r"(\d+)\s+passed", result.whitebox_output)
    if m:
        passed = int(m.group(1))
    m = _re.search(r"(\d+)\s+failed", result.whitebox_output)
    if m:
        failed = int(m.group(1))

    version = create_version(
        project_id=project_id,
        snapshot_id=snapshot_id,
        change_type="create" if mode == "create" else "modify",
        change_summary=result.summary or f"{passed} passed, {failed} failed",
        change_source="tdd",
        batch_id=batch.batch_id,
        pipeline_result_json=json.dumps({
            "success": result.success,
            "whitebox_passed": result.whitebox_passed,
            "blackbox_passed": result.blackbox_passed,
            "retry_count": result.retry_count,
        }),
        openspec_dir=batch.batch_dir / "openspec",
    )

    test_files, test_cases = extract_test_metadata(workspace)
    save_test_suite(
        version_id=version.id,
        test_files=test_files,
        test_cases=test_cases,
        total_tests=len(test_cases),
        passed=passed,
        failed=failed,
    )

    log_event(
        project_id, "version_created",
        f"v{version.version_number}: {passed} passed, {failed} failed",
        version_id=version.id,
    )

    # Auto-bind git commit if workspace is a git repo
    _try_bind_git_commit(version, workspace)

    # Architecture health check (respects management_level)
    _try_health_check(version, project_id, batch.project_type, db_modules if snapshot_id else [])

    snap_info = f", snapshot={snapshot_id[:8]}" if snapshot_id else ""
    print(f"  [wiki] Created version {version.version_number} "
          f"({len(test_cases)} tests, {len(test_files)} files{snap_info})")


def run_batch_tdd(batch: BatchState, llm: LLM, mode: str = "create") -> TDDResult:
    """Run a single TDD pipeline for the entire batch.

    Args:
        mode: "create" for greenfield, "modify" for change pipeline.
    """
    print(f"[PM] Running TDD pipeline for batch (mode={mode}, type={batch.project_type})")
    batch.status = "in_progress"
    _save_progress(batch)

    # Workspace at batch level
    workspace = batch.batch_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    ws_openspec = workspace / "openspec"
    if ws_openspec.exists():
        shutil.rmtree(ws_openspec)
    shutil.copytree(batch.batch_dir / "openspec", ws_openspec)

    # Agent logs at batch level
    log_dir = batch.batch_dir / "agent_logs"
    log_dir.mkdir(exist_ok=True)

    try:
        result = run_tdd_pipeline(
            workspace=workspace,
            llm=llm,
            mode=mode,
            log_dir=log_dir,
            project_type=batch.project_type,
        )
    except Exception as e:
        batch.status = "failed"
        batch.error = f"TDD pipeline error: {e}"
        _save_progress(batch)
        _write_json(batch.batch_dir / "result.json", {"error": str(e)})
        return TDDResult(success=False, summary=str(e))

    # Save result
    _write_json(batch.batch_dir / "result.json", {
        "success": result.success,
        "whitebox_passed": result.whitebox_passed,
        "blackbox_passed": result.blackbox_passed,
        "retry_count": result.retry_count,
        "summary": result.summary,
        "files_created": result.files_created,
        "test_files": result.test_files,
    })

    batch.status = "completed" if result.success else "failed"
    if not result.success:
        batch.error = result.summary
    _save_progress(batch)

    # --- Wiki version (track architecture + test suite state) ---
    if result.success:
        try:
            _create_wiki_version(batch, workspace, result, mode)
        except Exception as wiki_err:
            print(f"  [!] Wiki version creation failed: {wiki_err}")

    status_icon = "✓" if result.success else "✗"
    print(f"  [{status_icon}] Batch TDD — {batch.status}")
    return result


def run_batch(
    pm_root: str | Path,
    project_name: str,
    user_input: str,
    llm: LLM | None = None,
    project_type: str = "python",
) -> BatchState:
    """End-to-end: create batch → generate specs → parse tasks → run TDD pipeline."""
    if llm is None:
        llm = create_llm()

    # Step 1: Create batch
    batch = create_batch(pm_root, project_name, user_input, project_type=project_type)

    # Step 2: Generate openspec docs
    batch = run_spec_generation(batch, llm)
    if batch.status == "failed":
        return batch

    # Step 3: Parse tasks (for display/tracking only)
    prepare_tasks(batch)

    # Step 4: Run single TDD pipeline for entire batch
    result = run_batch_tdd(batch, llm)

    completed = sum(1 for t in batch.tasks if t.status == "completed")
    print(f"[PM] Batch finished: {batch.status} (TDD {'passed' if result.success else 'failed'}, "
          f"{len(batch.tasks)} tasks tracked)")
    return batch


def _write_mid_report_placeholder(
    batch: BatchState,
    *,
    auto_continue_sota: bool,
    mvp_summary: str,
) -> None:
    """Persist MVP mid-report hook payload (placeholder until channel is integrated)."""
    payload = {
        "run_id": batch.batch_id,
        "checkpoint": "mvp_uploaded",
        "summary": mvp_summary,
        "decision_required": "continue_to_sota|stop_after_mvp",
        "default_decision_when_unavailable": (
            "continue_to_sota" if auto_continue_sota else "stop_after_mvp"
        ),
        "created_at": _now_iso(),
    }
    _write_json(batch.batch_dir / "mid_report_hook.json", payload)


def run_batch_phased(
    pm_root: str | Path,
    project_name: str,
    user_input: str,
    llm: LLM | None = None,
    project_type: str = "python",
    *,
    auto_continue_sota: bool = True,
    enable_research_agent: bool = True,
    research_timebox_minutes: int = 8,
    auto_approve_research: bool = True,
) -> BatchState:
    """Phased pipeline: research -> MVP -> mvp_uploaded -> SOTA.

    Main goal:
    - Integrate research agent planning (MVP and SOTA options).
    - Execute MVP first as verifiable intermediate state.
    - Emit mid-report placeholder and continue to SOTA by default.

    Args:
        auto_approve_research: If False, pipeline pauses after research
            for human review. Use approve_review()/reject_review() to resume.
    """
    if llm is None:
        llm = create_llm()

    # Step 1: Create batch
    batch = create_batch(pm_root, project_name, user_input, project_type=project_type)
    _append_stage_event(batch, stage="init", event="batch_created")
    _append_stage_event(
        batch,
        stage="requirement",
        event="requirement_received",
        details={"project_name": project_name, "input_length": len(user_input)},
    )

    clarified_requirement = user_input.strip()
    _write_clarification_artifact(batch, clarified_requirement)
    _append_stage_event(
        batch,
        stage="clarification",
        event="clarification_completed",
        details={"artifact": "clarification.md"},
    )

    # Step 2: Research planning
    if enable_research_agent:
        active_plan = run_research_planning(
            batch,
            llm,
            trigger_type="kickoff_mvp_sota",
            timebox_minutes=research_timebox_minutes,
            enable_external_research=True,
            problem_statement=clarified_requirement,
        )
    else:
        active_plan = default_research_plan(clarified_requirement, trigger_type="kickoff_mvp_sota")
        _save_research_artifacts(
            batch,
            active_plan,
            trigger_type="kickoff_mvp_sota",
            timebox_minutes=research_timebox_minutes,
            enable_external_research=False,
            problem_statement=clarified_requirement,
        )
    _append_stage_event(
        batch,
        stage="research",
        event="research_completed",
        details={"trigger": "kickoff_mvp_sota", "recommended_option": active_plan.recommended_option},
    )
    _append_stage_event(
        batch,
        stage="selection",
        event="option_selected",
        details={"recommended_option": active_plan.recommended_option},
    )
    _append_stage_event(
        batch,
        stage="selection",
        event="mvp_scope_extracted",
        details={
            "mvp_scope_count": len(active_plan.mvp_scope),
            "mvp_extracted_from_sota": bool(active_plan.mvp_extracted_from_sota),
        },
    )

    # --- Human review checkpoint (if not auto-approved) ---
    if not auto_approve_research:
        review_artifacts = ["research/summary.md", "research/result.json"]
        _write_review_checkpoint(batch, "research_review", review_artifacts)
        _append_stage_event(batch, stage="research", event="awaiting_review")
        batch.status = "awaiting_review"
        _save_progress(batch)
        return batch

    def _run_stage_once(stage: str, stage_input: str, mode: str) -> tuple[bool, TDDResult | None, str]:
        nonlocal batch
        _append_stage_event(batch, stage=stage, event="spec_generation_start")
        batch = run_spec_generation(batch, llm, user_input_override=stage_input, stage_name=stage)
        if batch.status == "failed":
            err = batch.error or f"{stage} spec_generation_failed"
            _append_quality_gate(
                batch,
                stage=stage,
                gate="spec_generation",
                passed=False,
                details={"error": err},
            )
            _append_stage_event(batch, stage=stage, event="spec_generation_failed", details={"error": err})
            return False, None, err

        prepare_tasks(batch)
        _append_stage_event(batch, stage=stage, event="tdd_start")
        result = run_batch_tdd(batch, llm, mode=mode)
        _append_quality_gate(
            batch,
            stage=stage,
            gate="tdd",
            passed=bool(result.success),
            details={
                "mode": mode,
                "summary": result.summary,
                "whitebox_passed": result.whitebox_passed,
                "blackbox_passed": result.blackbox_passed,
                "retry_count": result.retry_count,
            },
        )
        _append_stage_event(
            batch,
            stage=stage,
            event="tdd_done",
            details={"success": result.success, "summary": result.summary},
        )
        if result.success:
            return True, result, ""
        return False, result, result.summary or f"{stage} tdd_failed"

    # Step 3: MVP stage
    mvp_deadlock_recovered = False
    mvp_input = _build_stage_requirement(clarified_requirement, active_plan, "mvp")
    mvp_ok, mvp_result, mvp_err = _run_stage_once("mvp", mvp_input, "create")
    if not mvp_ok and enable_research_agent:
        _append_stage_event(
            batch,
            stage="mvp",
            event="deadlock_resolution_start",
            details={"last_error": mvp_err},
        )
        active_plan = run_research_planning(
            batch,
            llm,
            trigger_type="deadlock_resolution",
            timebox_minutes=research_timebox_minutes,
            enable_external_research=True,
            problem_statement=mvp_input,
            local_attempt_summary=f"stage=mvp; mode=create; error={mvp_err}",
        )
        _append_stage_event(
            batch,
            stage="mvp",
            event="deadlock_resolution_done",
            details={"sources": len(active_plan.sources)},
        )
        mvp_deadlock_recovered = True
        mvp_input = _build_stage_requirement(clarified_requirement, active_plan, "mvp")
        mvp_ok, mvp_result, mvp_err = _run_stage_once("mvp", mvp_input, "create")

    if not mvp_ok or mvp_result is None:
        _write_stage_checkpoint(batch, current_stage="mvp", gate_passed=False)
        batch.status = "failed"
        batch.error = mvp_err
        _save_progress(batch)
        _write_exit_conditions(
            batch,
            current_stage="mvp",
            passed=False,
            reasons=[mvp_err],
            required_artifacts=["stage_checkpoint.json", "quality_gates.json"],
        )
        return batch

    # MVP intermediate checkpoint + mid-report placeholder
    _append_stage_event(
        batch,
        stage="mvp",
        event="mvp_implementation_completed",
        details={"summary": mvp_result.summary},
    )
    mvp_artifacts = ["openspec/proposal.md", "openspec/design.md", "openspec/tasks.md", "openspec/spec.md"]
    _write_stage_checkpoint(batch, current_stage="mvp_uploaded", gate_passed=True, artifact_refs=mvp_artifacts)
    _write_mid_report_placeholder(
        batch,
        auto_continue_sota=auto_continue_sota,
        mvp_summary=mvp_result.summary,
    )
    _append_stage_event(
        batch,
        stage="mvp_uploaded",
        event="checkpoint_written",
        details={"auto_continue_sota": auto_continue_sota},
    )

    if not auto_continue_sota:
        batch.status = "completed"
        _save_progress(batch)
        _write_stage_checkpoint(batch, current_stage="done", gate_passed=True, artifact_refs=mvp_artifacts)
        _write_exit_conditions(
            batch,
            current_stage="done",
            passed=True,
            reasons=["mvp_completed_stop_after_mvp"],
            required_artifacts=[
                "mid_report_hook.json",
                "stage_checkpoint.json",
                "quality_gates.json",
                *mvp_artifacts,
            ],
        )
        return batch

    # Step 4: SOTA stage (default auto-continue)
    sota_deadlock_recovered = False
    sota_input = _build_stage_requirement(clarified_requirement, active_plan, "sota")
    sota_ok, sota_result, sota_err = _run_stage_once("sota", sota_input, "modify")
    if not sota_ok and enable_research_agent:
        _append_stage_event(
            batch,
            stage="sota",
            event="deadlock_resolution_start",
            details={"last_error": sota_err},
        )
        active_plan = run_research_planning(
            batch,
            llm,
            trigger_type="deadlock_resolution",
            timebox_minutes=research_timebox_minutes,
            enable_external_research=True,
            problem_statement=sota_input,
            local_attempt_summary=f"stage=sota; mode=modify; error={sota_err}",
        )
        _append_stage_event(
            batch,
            stage="sota",
            event="deadlock_resolution_done",
            details={"sources": len(active_plan.sources)},
        )
        sota_deadlock_recovered = True
        sota_input = _build_stage_requirement(clarified_requirement, active_plan, "sota")
        sota_ok, sota_result, sota_err = _run_stage_once("sota", sota_input, "modify")

    if sota_result is None:
        batch.status = "failed"
        batch.error = sota_err
        _write_stage_checkpoint(batch, current_stage="sota", gate_passed=False)
        _save_progress(batch)
        _write_exit_conditions(
            batch,
            current_stage="sota",
            passed=False,
            reasons=[sota_err],
            required_artifacts=["mid_report_hook.json", "stage_checkpoint.json", "quality_gates.json"],
        )
        return batch

    if sota_ok and sota_result.success:
        batch.status = "completed"
        _append_stage_event(
            batch,
            stage="sota",
            event="sota_implementation_completed",
            details={"summary": sota_result.summary},
        )
        _write_stage_checkpoint(batch, current_stage="done", gate_passed=True)
    else:
        batch.status = "failed"
        batch.error = sota_err
        _write_stage_checkpoint(batch, current_stage="sota", gate_passed=False)
    _save_progress(batch)

    _write_json(batch.batch_dir / "phase_results.json", {
        "mvp": {
            "success": mvp_result.success,
            "summary": mvp_result.summary,
            "whitebox_passed": mvp_result.whitebox_passed,
            "blackbox_passed": mvp_result.blackbox_passed,
        },
        "sota": {
            "success": sota_result.success,
            "summary": sota_result.summary,
            "whitebox_passed": sota_result.whitebox_passed,
            "blackbox_passed": sota_result.blackbox_passed,
        },
        "deadlock_recovery": {
            "mvp": mvp_deadlock_recovered,
            "sota": sota_deadlock_recovered,
        },
        "auto_continue_sota": auto_continue_sota,
    })
    _write_exit_conditions(
        batch,
        current_stage="done" if batch.status == "completed" else "sota",
        passed=batch.status == "completed",
        reasons=[] if batch.status == "completed" else [batch.error or "sota_failed"],
        required_artifacts=[
            "mid_report_hook.json",
            "stage_checkpoint.json",
            "quality_gates.json",
            "phase_results.json",
            *mvp_artifacts,
        ],
    )
    return batch


def load_batch(batch_dir: str | Path) -> BatchState:
    """Load a BatchState from an existing batch directory."""
    batch_dir = Path(batch_dir)
    progress = _read_json(batch_dir / "progress.json")

    batch = BatchState(
        batch_id=progress["batch_id"],
        project_name=progress["project_name"],
        batch_dir=batch_dir,
        status=progress.get("status", "pending"),
        project_type=progress.get("project_type", "python"),
    )

    # Reload tasks from task.json files
    tasks_root = batch_dir / "tasks"
    if tasks_root.exists():
        task_dirs = sorted(tasks_root.iterdir())
        for td in task_dirs:
            if not td.is_dir():
                continue
            tj = td / "task.json"
            if not tj.exists():
                continue
            data = _read_json(tj)
            batch.tasks.append(TaskState(
                id=data["id"],
                title=data["title"],
                description=data.get("description", ""),
                status=data["status"],
                dependencies=data.get("dependencies", []),
                assigned_module=data.get("assigned_module"),
                task_dir=td,
            ))

    return batch


def resume_batch(
    batch_dir: str | Path,
    llm: LLM | None = None,
) -> BatchState:
    """Resume a batch — re-run TDD if not yet completed."""
    if llm is None:
        llm = create_llm()

    batch = load_batch(batch_dir)

    if batch.status == "completed":
        print(f"[PM] Batch {batch.batch_id} already completed")
        return batch

    print(f"[PM] Resuming batch {batch.batch_id}")

    result = run_batch_tdd(batch, llm)
    print(f"[PM] Resume finished: {batch.status} (TDD {'passed' if result.success else 'failed'})")
    return batch


# =============================================================================
# Change (brownfield) pipeline
# =============================================================================

def create_change_batch(
    pm_root: str | Path,
    project_name: str,
    workspace_path: str | Path,
    change_request: str,
    project_type: str = "python",
) -> BatchState:
    """Create a change batch from an existing workspace + change request.

    Copies the existing workspace and openspec into the new batch directory.
    """
    pm_root = Path(pm_root)
    pm_root.mkdir(parents=True, exist_ok=True)
    workspace_path = Path(workspace_path)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"{timestamp}_change_{_slugify(project_name)}"
    batch_dir = pm_root / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    # Save change request
    (batch_dir / "change_request.md").write_text(
        f"# Change Request: {project_name}\n\n{change_request}\n",
        encoding="utf-8",
    )

    # Copy existing workspace
    ws_dest = batch_dir / "workspace"
    shutil.copytree(workspace_path, ws_dest)

    # Copy openspec if it exists in workspace
    ws_openspec = ws_dest / "openspec"
    if ws_openspec.exists():
        openspec_dest = batch_dir / "openspec"
        if openspec_dest.exists():
            shutil.rmtree(openspec_dest)
        shutil.copytree(ws_openspec, openspec_dest)

    # Mark as change batch
    batch = BatchState(
        batch_id=batch_id,
        project_name=project_name,
        batch_dir=batch_dir,
        status="pending",
        project_type=project_type,
    )
    _write_json(batch_dir / "batch_meta.json", {
        "type": "change",
        "source_workspace": str(workspace_path),
        "project_type": project_type,
    })
    _save_progress(batch)
    print(f"[PM] Created change batch: {batch_dir}")
    return batch


def run_change_analysis(
    batch: BatchState,
    llm: LLM,
) -> ImpactAnalysis:
    """Phase 1+2: Snapshot code + LLM impact analysis.

    Creates code snapshot, runs impact analysis against design.md + spec.md,
    runs architecture guard, saves results.
    """
    print("[PM] Running change analysis (snapshot → impact)")
    batch.status = "in_progress"
    _save_progress(batch)

    workspace = batch.batch_dir / "workspace"
    openspec_dir = batch.batch_dir / "openspec"

    # Phase 1: Code snapshot
    snapshot = create_snapshot(workspace, batch.project_name)
    save_snapshot(snapshot, batch.batch_dir / "snapshot.json")
    print(f"  Snapshot: {len(snapshot.modules)} modules")

    # Read current openspec docs
    design_md = ""
    spec_md = ""
    if (openspec_dir / "design.md").exists():
        design_md = (openspec_dir / "design.md").read_text(encoding="utf-8")
    if (openspec_dir / "spec.md").exists():
        spec_md = (openspec_dir / "spec.md").read_text(encoding="utf-8")

    change_request = (batch.batch_dir / "change_request.md").read_text(encoding="utf-8")

    # Phase 2: Impact analysis
    impact = run_impact_analysis(
        change_request=change_request,
        snapshot=snapshot,
        design_md=design_md,
        spec_md=spec_md,
        llm=llm,
    )

    # Architecture guard (advisory)
    # Parse design into structured form for health check
    from toyshop.tdd_pipeline import _parse_design_modules, _parse_design_interfaces
    if design_md:
        from types import SimpleNamespace
        raw_modules = _parse_design_modules(design_md)
        raw_interfaces = _parse_design_interfaces(design_md)
        # Build lightweight objects for health check
        modules = [SimpleNamespace(
            id=m.get("id", ""), name=m.get("name", ""),
            responsibilities=m.get("responsibilities", []),
            dependencies=m.get("dependencies", []),
        ) for m in raw_modules]
        interfaces = [SimpleNamespace(
            id=i.get("id", ""), name=i.get("name", ""),
            module_id=i.get("module_id", ""),
        ) for i in raw_interfaces]
        design_obj = SimpleNamespace(modules=modules, interfaces=interfaces)
        arch_warnings = check_architecture_health(design_obj)
        impact.architecture_warnings = arch_warnings
        if arch_warnings:
            print(f"  Architecture warnings: {len(arch_warnings)}")
            for w in arch_warnings:
                print(f"    ⚠ {w}")

    save_impact(impact, batch.batch_dir / "impact.json")
    _save_progress(batch)

    print(f"  Impact: {len(impact.affected_modules)} modules, "
          f"{len(impact.affected_interfaces)} interfaces, "
          f"{len(impact.affected_scenarios)} scenarios affected")
    if impact.new_modules:
        print(f"  New modules: {len(impact.new_modules)}")

    return impact


def run_spec_evolution(
    batch: BatchState,
    impact: ImpactAnalysis,
    llm: LLM,
) -> BatchState:
    """Phase 3: Update openspec docs based on impact analysis.

    Evolves proposal, design, tasks, and spec in-place.
    Verifies that unchanged parts are preserved.
    """
    print("[PM] Running spec evolution")
    openspec_dir = batch.batch_dir / "openspec"
    change_request = (batch.batch_dir / "change_request.md").read_text(encoding="utf-8")

    # Evolve proposal
    proposal_path = openspec_dir / "proposal.md"
    if proposal_path.exists():
        old_proposal = proposal_path.read_text(encoding="utf-8")
        new_proposal = evolve_proposal(old_proposal, change_request, impact, llm)
        proposal_path.write_text(new_proposal, encoding="utf-8")
        print("  Updated proposal.md")

    # Evolve design
    design_path = openspec_dir / "design.md"
    if design_path.exists():
        old_design = design_path.read_text(encoding="utf-8")
        new_design = evolve_design(old_design, impact, llm)
        design_path.write_text(new_design, encoding="utf-8")
        print("  Updated design.md")

        # Verify evolution
        warnings = verify_evolution(old_design, new_design, impact)
        if warnings:
            print(f"  Evolution warnings: {len(warnings)}")
            for w in warnings:
                print(f"    ⚠ {w}")

    # Evolve tasks (generate change-only tasks)
    new_tasks = evolve_tasks(impact, llm)
    (openspec_dir / "tasks.md").write_text(new_tasks, encoding="utf-8")
    print("  Updated tasks.md")

    # Evolve spec
    spec_path = openspec_dir / "spec.md"
    if spec_path.exists():
        old_spec = spec_path.read_text(encoding="utf-8")
        new_spec = evolve_spec(old_spec, impact, llm)
        spec_path.write_text(new_spec, encoding="utf-8")
        print("  Updated spec.md")

    # Copy updated openspec to workspace
    ws_openspec = batch.batch_dir / "workspace" / "openspec"
    if ws_openspec.exists():
        shutil.rmtree(ws_openspec)
    shutil.copytree(openspec_dir, ws_openspec)

    _save_progress(batch)
    return batch
