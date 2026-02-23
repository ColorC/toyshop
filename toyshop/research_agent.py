"""Research Agent integration for kickoff planning (MVP + SOTA).

Primary goal:
- Provide a stable integration point for external research (GPT Researcher).
- Always return a structured plan even when external search is unavailable.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any

from toyshop.llm import LLM, chat_with_tool


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


def _run_async(coro):
    """Run coroutine in sync contexts. Returns None on loop conflicts."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # If we're already in an event loop (unlikely in current PM CLI path),
        # skip external research instead of failing pipeline.
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
    except Exception:
        return "", []

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
            summary = await researcher.quick_search(query, aggregated_summary=True)
        except Exception:
            summary = ""

        # Best effort source URLs
        try:
            raw_results = await researcher.quick_search(query, aggregated_summary=False)
            if isinstance(raw_results, list):
                for item in raw_results:
                    if isinstance(item, dict):
                        url = item.get("url")
                        if isinstance(url, str) and url:
                            sources.append(url)
            sources = sources[:10]
        except Exception:
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
    except Exception:
        result = None

    if not result:
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
        if not plan.mvp_option or not plan.sota_option:
            plan = default_research_plan(user_input, trigger_type=trigger_type)

    # Merge sources from external research best effort.
    if external_sources:
        plan.sources = external_sources
    if external_summary and not plan.external_summary:
        plan.external_summary = external_summary
    return plan

