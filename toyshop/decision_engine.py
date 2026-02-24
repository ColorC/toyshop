"""Decision engine — decide whether to create a new project or modify an existing one.

Given a decomposed requirement and a directory of existing projects,
uses lightweight scanning + LLM to decide the best action.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from toyshop.decomposer import DecompositionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ProjectCandidate:
    """An existing project evaluated for modification."""

    name: str
    path: str
    summary: str = ""
    relevance: float = 0.0


@dataclass
class Decision:
    """Result of the create-vs-modify decision."""

    action: str  # "create" | "modify"
    target: str | None = None
    target_path: str | None = None
    rationale: str = ""
    candidates: list[ProjectCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Project scanning
# ---------------------------------------------------------------------------


def _scan_project_summary(project_dir: Path) -> str:
    """Quick scan of a project directory to build a summary."""
    parts = []

    # Check for key config files
    for name in ["build.gradle", "pyproject.toml", "package.json", "Cargo.toml"]:
        cfg = project_dir / name
        if cfg.is_file():
            parts.append(f"Config: {name}")
            # Read first 20 lines for metadata
            try:
                lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()[:20]
                parts.append("\n".join(lines))
            except OSError:
                pass
            break

    # Check for fabric.mod.json (MC mods)
    for fmj in project_dir.rglob("fabric.mod.json"):
        try:
            import json
            data = json.loads(fmj.read_text(encoding="utf-8"))
            parts.append(f"Mod ID: {data.get('id', '?')}")
            parts.append(f"Description: {data.get('description', '?')}")
        except Exception:
            pass
        break

    # List top-level source files
    src_files = []
    for ext in ["*.py", "*.java", "*.ts", "*.js"]:
        src_files.extend(project_dir.rglob(ext))
    if src_files:
        names = sorted(set(f.name for f in src_files[:20]))
        parts.append(f"Source files ({len(src_files)} total): {', '.join(names[:10])}")

    return "\n".join(parts) if parts else "Empty or unrecognized project"


def analyze_existing_projects(
    projects_dir: Path,
    project_type: str = "",
) -> list[ProjectCandidate]:
    """Scan a directory for existing projects and build summaries.

    Each subdirectory with a build config file is treated as a project.
    """
    if not projects_dir.is_dir():
        return []

    candidates = []
    for child in sorted(projects_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        # Check if it looks like a project
        has_config = any(
            (child / name).is_file()
            for name in ["build.gradle", "pyproject.toml", "package.json",
                         "Cargo.toml", "pom.xml", "Makefile"]
        )
        if not has_config:
            continue

        summary = _scan_project_summary(child)
        candidates.append(ProjectCandidate(
            name=child.name,
            path=str(child),
            summary=summary,
        ))

    return candidates


# ---------------------------------------------------------------------------
# LLM decision
# ---------------------------------------------------------------------------

_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "modify"],
        },
        "target_project": {
            "type": "string",
            "description": "Name of existing project to modify (if action=modify).",
        },
        "rationale": {
            "type": "string",
            "description": "Why this decision was made.",
        },
        "relevance_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["project", "score"],
            },
        },
    },
    "required": ["action", "rationale"],
}


def decide_create_or_modify(
    decomposition: DecompositionResult,
    candidates: list[ProjectCandidate],
    llm: Any,
) -> Decision:
    """Decide whether to create a new project or modify an existing one."""
    if not candidates:
        return Decision(
            action="create",
            rationale="No existing projects found",
            candidates=[],
        )

    from toyshop.llm import chat_with_tool

    # Build context
    aspects_text = "\n".join(
        f"  - [{a.id}] {a.title} ({a.aspect_type}/{a.category}): {a.description}"
        for a in decomposition.aspects
    )
    candidates_text = "\n".join(
        f"  - {c.name}:\n    {c.summary[:300]}"
        for c in candidates
    )

    system = (
        "You are deciding whether a new requirement should be implemented as a "
        "NEW project or as a MODIFICATION to an existing project.\n\n"
        "Choose 'modify' if an existing project already covers >50% of the "
        "requirement's aspects and the new aspects fit naturally.\n"
        "Choose 'create' if the requirement is substantially different from "
        "all existing projects."
    )
    user = (
        f"Requirement: {decomposition.original_requirement}\n\n"
        f"Aspects:\n{aspects_text}\n\n"
        f"Existing projects:\n{candidates_text}\n\n"
        "Decide using the tool."
    )

    result = chat_with_tool(
        llm, system, user,
        "decide_action",
        "Decide whether to create a new project or modify an existing one.",
        _DECISION_SCHEMA,
    )

    if result is None:
        return Decision(
            action="create",
            rationale="LLM unavailable, defaulting to create",
            candidates=candidates,
        )

    # Apply relevance scores to candidates
    scores = {s["project"]: s["score"] for s in result.get("relevance_scores", [])}
    for c in candidates:
        c.relevance = scores.get(c.name, 0.0)

    target = result.get("target_project")
    target_path = None
    if target:
        for c in candidates:
            if c.name == target:
                target_path = c.path
                break

    return Decision(
        action=result.get("action", "create"),
        target=target,
        target_path=target_path,
        rationale=result.get("rationale", ""),
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def decision_to_dict(decision: Decision) -> dict:
    """Serialize Decision to JSON-compatible dict."""
    return {
        "action": decision.action,
        "target": decision.target,
        "target_path": decision.target_path,
        "rationale": decision.rationale,
        "candidates": [asdict(c) for c in decision.candidates],
    }


def decision_from_dict(data: dict) -> Decision:
    """Deserialize Decision from dict."""
    return Decision(
        action=data["action"],
        target=data.get("target"),
        target_path=data.get("target_path"),
        rationale=data.get("rationale", ""),
        candidates=[
            ProjectCandidate(**c) for c in data.get("candidates", [])
        ],
    )
