"""Requirement decomposer — LLM-driven decomposition into typed aspects.

Breaks high-level requirements into aspects tagged with type (logic/mechanism/content)
and category (server/client/shared for MC, general otherwise). Each aspect carries
search keywords for reference source scanning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any

from toyshop.reference import ReferenceConfig, ScanResult, scan_references

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RequirementAspect:
    """A single aspect of a decomposed requirement."""

    id: str
    title: str
    description: str
    aspect_type: str  # "logic" | "mechanism" | "content"
    category: str     # MC: "server" | "client" | "shared"; general: "general"
    keywords: list[str] = field(default_factory=list)
    priority: str = "must"  # "must" | "should" | "nice"


@dataclass
class DecompositionResult:
    """Result of decomposing a requirement."""

    original_requirement: str
    project_type: str
    aspects: list[RequirementAspect] = field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------------
# LLM decomposition
# ---------------------------------------------------------------------------

_DECOMPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "aspects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "aspect_type": {
                        "type": "string",
                        "enum": ["logic", "mechanism", "content"],
                    },
                    "category": {"type": "string"},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["must", "should", "nice"],
                    },
                },
                "required": ["id", "title", "description", "aspect_type", "keywords"],
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["aspects", "rationale"],
}

# Category hints for MC projects
_MC_CATEGORIES = {
    "server": ["logic", "game rule", "command", "event", "data", "rcon", "nbt",
               "registry", "block entity", "tick", "spawn", "loot"],
    "client": ["render", "texture", "model", "gui", "hud", "particle", "sound",
               "animation", "screen", "shader", "resource pack"],
    "shared": ["block", "item", "entity", "recipe", "tag", "network", "packet"],
}


def decompose_requirement(
    requirement: str,
    project_type: str,
    llm: Any,
    *,
    context: str = "",
) -> DecompositionResult:
    """Decompose a high-level requirement into typed aspects.

    For java-minecraft projects, aspects are auto-categorized as
    server/client/shared based on keywords.
    """
    from toyshop.llm import chat_with_tool

    is_mc = "minecraft" in project_type.lower()

    category_hint = ""
    if is_mc:
        category_hint = (
            "\nFor Minecraft mod projects, categorize each aspect as:\n"
            "- 'server': game logic, commands, events, data processing\n"
            "- 'client': rendering, textures, models, GUI, particles, sounds\n"
            "- 'shared': blocks, items, entities, recipes, networking\n"
        )

    system = (
        "You are a software architect decomposing a requirement into implementation aspects.\n"
        "Each aspect should be a distinct, implementable piece of work.\n"
        "Tag each aspect with:\n"
        "- aspect_type: 'logic' (algorithms, game rules), 'mechanism' (API/framework usage), "
        "or 'content' (assets, data, configuration)\n"
        "- keywords: search terms for finding reference implementations\n"
        "- priority: 'must' (core), 'should' (important), 'nice' (optional)\n"
        f"{category_hint}"
    )
    user = f"Requirement: {requirement}\nProject type: {project_type}\n"
    if context:
        user += f"\nExisting context:\n{context}\n"
    user += "\nDecompose this requirement into aspects using the tool."

    result = chat_with_tool(
        llm, system, user,
        "decompose_requirement",
        "Decompose a requirement into typed aspects.",
        _DECOMPOSE_SCHEMA,
    )

    if result is None:
        logger.warning("LLM returned None for decomposition, using fallback")
        return _fallback_decomposition(requirement, project_type)

    aspects = []
    for a in result.get("aspects", []):
        category = a.get("category", "general")
        if is_mc and category == "general":
            category = _infer_mc_category(a.get("keywords", []), a.get("description", ""))
        aspects.append(RequirementAspect(
            id=a["id"],
            title=a["title"],
            description=a["description"],
            aspect_type=a["aspect_type"],
            category=category,
            keywords=a.get("keywords", []),
            priority=a.get("priority", "must"),
        ))

    return DecompositionResult(
        original_requirement=requirement,
        project_type=project_type,
        aspects=aspects,
        rationale=result.get("rationale", ""),
    )


def _infer_mc_category(keywords: list[str], description: str) -> str:
    """Infer MC category from keywords and description."""
    text = " ".join(keywords).lower() + " " + description.lower()
    scores = {"server": 0, "client": 0, "shared": 0}
    for cat, terms in _MC_CATEGORIES.items():
        for term in terms:
            if term in text:
                scores[cat] += 1
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    return best if scores[best] > 0 else "shared"


def _fallback_decomposition(requirement: str, project_type: str) -> DecompositionResult:
    """Simple fallback when LLM is unavailable."""
    words = requirement.lower().split()
    keywords = [w for w in words if len(w) > 3][:5]
    return DecompositionResult(
        original_requirement=requirement,
        project_type=project_type,
        aspects=[
            RequirementAspect(
                id="asp_1",
                title="Core Implementation",
                description=requirement,
                aspect_type="logic",
                category="general",
                keywords=keywords,
                priority="must",
            ),
        ],
        rationale="Fallback: single-aspect decomposition",
    )


# ---------------------------------------------------------------------------
# Aspect-to-source matching
# ---------------------------------------------------------------------------


def match_aspects_to_sources(
    decomposition: DecompositionResult,
    config: ReferenceConfig,
    llm: Any,
) -> dict[str, list[ScanResult]]:
    """For each aspect, scan reference sources and return results.

    Returns {aspect_id: [ScanResult, ...]}.
    """
    results: dict[str, list[ScanResult]] = {}
    for aspect in decomposition.aspects:
        scan_results = scan_references(
            aspect_id=aspect.id,
            aspect_type=aspect.aspect_type,
            keywords=aspect.keywords,
            config=config,
            llm=llm,
        )
        results[aspect.id] = scan_results
    return results


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def decomposition_to_dict(result: DecompositionResult) -> dict:
    """Serialize DecompositionResult to JSON-compatible dict."""
    return {
        "original_requirement": result.original_requirement,
        "project_type": result.project_type,
        "rationale": result.rationale,
        "aspects": [asdict(a) for a in result.aspects],
    }


def decomposition_from_dict(data: dict) -> DecompositionResult:
    """Deserialize DecompositionResult from dict."""
    return DecompositionResult(
        original_requirement=data["original_requirement"],
        project_type=data["project_type"],
        rationale=data.get("rationale", ""),
        aspects=[
            RequirementAspect(**a) for a in data.get("aspects", [])
        ],
    )
