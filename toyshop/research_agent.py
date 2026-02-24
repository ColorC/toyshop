"""Research Agent integration for kickoff planning (MVP + SOTA).

Primary goal:
- Provide a stable integration point for external research (GPT Researcher).
- Always return a structured plan even when external search is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from toyshop.llm import LLM, chat_with_tool

logger = logging.getLogger(__name__)


@dataclass
class ResearchPlan:
    """Structured planning output for phased execution."""

    trigger_type: str
    problem_statement: str
    mvp_option: str
    sota_option: str
    mvp_extracted_from_sota: bool = True
    mvp_scope: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    recommended_option: str = "mvp_first_then_sota"
    adoption_plan: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    external_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_research_plan(user_input: str, trigger_type: str = "kickoff_mvp_sota") -> ResearchPlan:
    """Build a safe default plan when external research or LLM planning fails."""
    return ResearchPlan(
        trigger_type=trigger_type,
        problem_statement=user_input.strip(),
        mvp_option=(
            "Implement the smallest end-to-end workflow that can be verified by tests "
            "and produce required artifacts."
        ),
        sota_option=(
            "After MVP is stable, enhance architecture quality, observability, and "
            "edge-case handling using current best practices."
        ),
        mvp_scope=[
            "core happy-path flow",
            "minimal interfaces",
            "must-have tests and gates",
        ],
        tradeoffs=[
            "MVP has lower implementation risk and faster validation",
            "SOTA has higher quality but requires more iteration cost",
        ],
        adoption_plan=[
            "stage_1_mvp",
            "checkpoint_mvp_uploaded",
            "stage_2_sota",
        ],
        sources=[],
        external_summary="",
    )


def validate_research_plan(plan: ResearchPlan) -> list[str]:
    """Validate a research plan and return a list of warning strings.

    Returns empty list if the plan is valid.
    """
    warnings: list[str] = []
    if not plan.problem_statement.strip():
        warnings.append("problem_statement is empty")
    if not plan.mvp_option.strip():
        warnings.append("mvp_option is empty")
    if not plan.sota_option.strip():
        warnings.append("sota_option is empty")
    if not plan.mvp_scope:
        warnings.append("mvp_scope is empty (no scope items)")
    return warnings


def _run_async(coro):
    """Run coroutine in sync contexts. Returns None on loop conflicts."""
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        # If we're already in an event loop (unlikely in current PM CLI path),
        # skip external research instead of failing pipeline.
        logger.info("Skipping async research (event loop conflict): %s", e)
        return None


def try_gpt_researcher_summary(
    query: str,
    *,
    timebox_minutes: int = 8,
    enabled: bool = True,
) -> tuple[str, list[str]]:
    """Try external web research via GPT Researcher.

    Returns:
        (summary, sources). Empty values on failure.
    """
    if not enabled:
        return "", []

    try:
        # Optional dependency and runtime integration.
        from gpt_researcher import GPTResearcher  # type: ignore
    except ImportError:
        logger.info("gpt-researcher not installed, skipping external research")
        return "", []
    except Exception as e:
        logger.warning("Failed to import gpt_researcher: %s", e)
        return "", []

    timeout_seconds = timebox_minutes * 60

    async def _run() -> tuple[str, list[str]]:
        researcher = GPTResearcher(
            query=query,
            report_type="research_report",
            report_source="web",
            verbose=False,
        )

        summary = ""
        sources: list[str] = []

        # Fast path: quick aggregated summary
        try:
            summary = await asyncio.wait_for(
                researcher.quick_search(query, aggregated_summary=True),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("GPT Researcher summary timed out after %ds", timeout_seconds)
            summary = ""
        except Exception as e:
            logger.warning("GPT Researcher summary failed: %s", e)
            summary = ""

        # Best effort source URLs
        try:
            raw_results = await asyncio.wait_for(
                researcher.quick_search(query, aggregated_summary=False),
                timeout=timeout_seconds,
            )
            if isinstance(raw_results, list):
                for item in raw_results:
                    if isinstance(item, dict):
                        url = item.get("url")
                        if isinstance(url, str) and url:
                            sources.append(url)
            sources = sources[:10]
        except asyncio.TimeoutError:
            logger.warning("GPT Researcher sources timed out after %ds", timeout_seconds)
            sources = []
        except Exception as e:
            logger.warning("GPT Researcher sources failed: %s", e)
            sources = []

        return summary, sources

    result = _run_async(_run())
    if not result:
        return "", []
    summary, sources = result

    # Keep context compact; timebox respected at orchestration layer.
    if len(summary) > 4000:
        summary = summary[:4000] + "\n... (truncated)"
    return summary, sources


def generate_kickoff_plan(
    *,
    user_input: str,
    llm: LLM,
    trigger_type: str = "kickoff_mvp_sota",
    enable_external_research: bool = True,
    timebox_minutes: int = 8,
) -> ResearchPlan:
    """Generate a structured MVP/SOTA plan.

    Strategy:
    1) Try GPT Researcher summary (optional).
    2) Ask local planning LLM to output structured JSON.
    3) Fallback to deterministic default plan.
    """
    external_summary, external_sources = try_gpt_researcher_summary(
        query=user_input,
        timebox_minutes=timebox_minutes,
        enabled=enable_external_research,
    )

    system = (
        "You are a software planning analyst. "
        "Produce a staged delivery plan with SOTA first, then extract MVP from SOTA. "
        "Execution order must be MVP first, then SOTA.\n"
        "Output only tool arguments via generate_research_plan_data."
    )

    user_content = (
        f"Trigger: {trigger_type}\n"
        f"Problem:\n{user_input}\n\n"
        f"External summary (if any):\n{external_summary or '(none)'}\n\n"
        "Requirements:\n"
        "- Provide SOTA option (full best-practice target)\n"
        "- Provide MVP option extracted from SOTA\n"
        "- Provide MVP scope list (modules/interfaces/tasks)\n"
        "- Provide tradeoffs and adoption plan\n"
        "- Keep it practical for current platform constraints\n"
    )

    schema = {
        "type": "object",
        "properties": {
            "problem_statement": {"type": "string"},
            "mvp_option": {"type": "string"},
            "sota_option": {"type": "string"},
            "mvp_extracted_from_sota": {"type": "boolean"},
            "mvp_scope": {"type": "array", "items": {"type": "string"}},
            "tradeoffs": {"type": "array", "items": {"type": "string"}},
            "recommended_option": {"type": "string"},
            "adoption_plan": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "problem_statement",
            "mvp_option",
            "sota_option",
            "mvp_scope",
            "tradeoffs",
            "adoption_plan",
        ],
    }

    try:
        result = chat_with_tool(
            llm=llm,
            system_prompt=system,
            user_content=user_content,
            tool_name="generate_research_plan_data",
            tool_description="Generate structured MVP/SOTA plan from requirement and research",
            tool_parameters=schema,
        )
    except Exception as e:
        logger.warning("LLM planning call failed: %s", e)
        result = None

    if not result:
        logger.info("LLM returned no result, using default research plan")
        plan = default_research_plan(user_input, trigger_type=trigger_type)
    else:
        plan = ResearchPlan(
            trigger_type=trigger_type,
            problem_statement=result.get("problem_statement", user_input.strip()),
            mvp_option=result.get("mvp_option", ""),
            sota_option=result.get("sota_option", ""),
            mvp_extracted_from_sota=bool(result.get("mvp_extracted_from_sota", True)),
            mvp_scope=result.get("mvp_scope", []) or [],
            tradeoffs=result.get("tradeoffs", []) or [],
            recommended_option=result.get("recommended_option", "mvp_first_then_sota"),
            adoption_plan=result.get("adoption_plan", []) or [],
            sources=[],
            external_summary=external_summary,
        )
        warnings = validate_research_plan(plan)
        if warnings:
            logger.warning("LLM plan validation failed: %s — falling back to default", warnings)
            plan = default_research_plan(user_input, trigger_type=trigger_type)

    # Merge sources from external research best effort.
    if external_sources:
        plan.sources = external_sources
    if external_summary and not plan.external_summary:
        plan.external_summary = external_summary
    return plan


# =============================================================================
# Stage 2: Structured SOTA criteria + MVP extraction
# =============================================================================


@dataclass
class ResearchSpec:
    """Structured spec derived from research plan + original requirements.

    Used to produce anchored, structured stage requirements that prevent
    research results from dominating the original user intent.
    """

    original_requirement: str
    sota_criteria: list[str]
    mvp_boundaries: list[str]
    deferred_to_sota: list[str]
    acceptance_criteria: list[str]
    architecture_constraints: list[str]
    risk_items: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_sota_criteria(
    user_input: str,
    external_summary: str,
    llm: LLM,
) -> list[str]:
    """Generate concrete, measurable SOTA criteria grounded in research.

    Each criterion must be a verifiable technical decision, not a vague
    "best practice" statement.
    """
    system = (
        "You are a software architecture analyst. "
        "Given a requirement and optional external research, produce a list of "
        "concrete, measurable SOTA (state-of-the-art) quality criteria.\n\n"
        "Rules:\n"
        "- Each criterion must be a specific, verifiable technical decision\n"
        "- BAD: 'use best practices' / 'ensure quality'\n"
        "- GOOD: 'Use connection pooling for database access' / "
        "'Implement retry with exponential backoff for HTTP calls'\n"
        "- Criteria must serve the original requirement, not drift into unrelated areas\n"
        "- Include 4-8 criteria covering: architecture, testing, error handling, performance\n"
        "- Use the external research as evidence, not as the sole driver"
    )

    user_content = (
        f"## Original Requirement\n{user_input}\n\n"
        f"## External Research (grounding evidence)\n"
        f"{external_summary or '(no external research available)'}\n\n"
        "Generate SOTA criteria that serve the original requirement above."
    )

    schema = {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of concrete, measurable SOTA criteria",
            },
        },
        "required": ["criteria"],
    }

    try:
        result = chat_with_tool(
            llm=llm,
            system_prompt=system,
            user_content=user_content,
            tool_name="generate_sota_criteria",
            tool_description="Generate concrete SOTA quality criteria",
            tool_parameters=schema,
        )
    except Exception as e:
        logger.warning("SOTA criteria generation failed: %s", e)
        result = None

    if result and result.get("criteria"):
        return result["criteria"]
    return ["End-to-end test coverage for core workflow", "Clean error handling with meaningful messages"]


def extract_mvp_from_sota(
    sota_criteria: list[str],
    user_input: str,
    llm: LLM,
) -> tuple[list[str], list[str]]:
    """Classify each SOTA criterion as MVP-essential or SOTA-only.

    Returns (mvp_boundaries, deferred_items).
    MVP-essential: required for the minimum end-to-end verifiable path.
    SOTA-only: deferred to the SOTA stage.
    """
    criteria_text = "\n".join(f"- {c}" for c in sota_criteria)

    system = (
        "You are a software delivery planner. "
        "Classify each SOTA criterion as either MVP-essential or SOTA-only.\n\n"
        "MVP-essential: required for the smallest end-to-end verifiable implementation.\n"
        "SOTA-only: improves quality but can be deferred.\n\n"
        "Rules:\n"
        "- At least 2 criteria must be MVP-essential\n"
        "- At least 1 criterion must be SOTA-only\n"
        "- Classification must serve the original requirement"
    )

    user_content = (
        f"## Original Requirement\n{user_input}\n\n"
        f"## SOTA Criteria to Classify\n{criteria_text}"
    )

    schema = {
        "type": "object",
        "properties": {
            "mvp_essential": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Criteria required for MVP",
            },
            "sota_only": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Criteria deferred to SOTA stage",
            },
        },
        "required": ["mvp_essential", "sota_only"],
    }

    try:
        result = chat_with_tool(
            llm=llm,
            system_prompt=system,
            user_content=user_content,
            tool_name="classify_criteria",
            tool_description="Classify SOTA criteria into MVP-essential and SOTA-only",
            tool_parameters=schema,
        )
    except Exception as e:
        logger.warning("MVP extraction failed: %s", e)
        result = None

    if result and result.get("mvp_essential"):
        return result["mvp_essential"], result.get("sota_only", [])

    # Fallback: first half MVP, rest SOTA
    mid = max(1, len(sota_criteria) // 2)
    return sota_criteria[:mid], sota_criteria[mid:]


def build_research_spec(
    user_input: str,
    plan: ResearchPlan,
    llm: LLM,
) -> ResearchSpec:
    """Orchestrate SOTA criteria generation and MVP extraction.

    Produces a structured ResearchSpec that anchors all output to the
    original requirement.
    """
    sota_criteria = generate_sota_criteria(
        user_input=user_input,
        external_summary=plan.external_summary,
        llm=llm,
    )

    mvp_boundaries, deferred = extract_mvp_from_sota(
        sota_criteria=sota_criteria,
        user_input=user_input,
        llm=llm,
    )

    # Derive acceptance criteria from MVP boundaries
    acceptance = [f"Verify: {b}" for b in mvp_boundaries]

    # Architecture constraints from research (if any)
    constraints = []
    if plan.external_summary:
        constraints.append(f"Research context: {plan.external_summary[:500]}")

    return ResearchSpec(
        original_requirement=user_input.strip(),
        sota_criteria=sota_criteria,
        mvp_boundaries=mvp_boundaries,
        deferred_to_sota=deferred,
        acceptance_criteria=acceptance,
        architecture_constraints=constraints,
        risk_items=plan.tradeoffs[:3] if plan.tradeoffs else [],
    )

