"""Requirement workflow nodes.

Each node is a function that takes an LLM and current state,
calls the LLM with a tool schema, and returns structured output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from toyshop.llm import LLM, chat_with_tool
from toyshop.openspec.types import (
    Capability,
    Risk,
    OpenSpecProposal,
    ProposalInput,
    Priority,
    Severity,
)
from toyshop.openspec.generator import generate_proposal, render_proposal_markdown

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class CollectedInfo:
    """Structured info extracted from user input."""

    domain: str = ""
    target_users: list[str] = field(default_factory=list)
    core_features: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    existing_context: list[str] = field(default_factory=list)


@dataclass
class Clarification:
    """A clarification question and optional answer."""

    question: str
    reason: str = ""
    answer: str | None = None


@dataclass
class RequirementState:
    """State for the requirement workflow."""

    user_input: str = ""
    project_name: str = ""

    # Stage outputs
    collected_info: CollectedInfo | None = None
    clarifications: list[Clarification] = field(default_factory=list)
    proposal: OpenSpecProposal | None = None
    proposal_markdown: str = ""

    # Control
    current_step: str = "analyze"
    error: str | None = None


# ---------------------------------------------------------------------------
# Tool Schemas
# ---------------------------------------------------------------------------

ANALYZE_INPUT_TOOL = {
    "type": "function",
    "function": {
        "name": "analyze_input",
        "description": "Extract structured information from user's project description",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "The business domain (e.g., 'e-commerce', 'healthcare')",
                },
                "target_users": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Who will use this software",
                },
                "core_features": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key features the software must have",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Technical or business constraints",
                },
                "existing_context": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Existing systems or code to integrate with",
                },
            },
            "required": ["domain", "target_users", "core_features"],
        },
    },
}

GENERATE_QUESTIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_questions",
        "description": "Generate clarifying questions to better understand requirements",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "reason": {"type": "string", "description": "Why this question matters"},
                        },
                        "required": ["question", "reason"],
                    },
                    "description": "Clarification questions",
                },
            },
            "required": ["questions"],
        },
    },
}

GENERATE_PROPOSAL_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_proposal_data",
        "description": "Generate OpenSpec proposal data from collected requirements",
        "parameters": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
                "background": {"type": "string", "description": "Project background and context"},
                "problem": {"type": "string", "description": "Problem to solve"},
                "goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Project goals",
                },
                "non_goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit out-of-scope items",
                },
                "capabilities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "priority": {"type": "string", "enum": ["must", "should", "could", "wont"]},
                        },
                        "required": ["name", "description", "priority"],
                    },
                },
                "impacted_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "risks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                            "mitigation": {"type": "string"},
                        },
                        "required": ["description", "severity", "mitigation"],
                    },
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "timeline": {"type": "string"},
            },
            "required": ["project_name", "background", "problem", "goals", "capabilities"],
        },
    },
}


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def analyze_input(llm: LLM, state: RequirementState) -> dict[str, Any]:
    """Analyze user input and extract structured info."""
    system = """你是一位经验丰富的需求分析师。你的任务是从用户的描述中提取结构化信息。
分析用户的需求描述，识别：
- 业务领域
- 目标用户
- 核心功能
- 约束条件
- 现有上下文

请使用 analyze_input 工具返回结构化数据。"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=f"项目名称: {state.project_name}\n\n用户描述:\n{state.user_input}",
        tool_name="analyze_input",
        tool_description="Extract structured information from user's project description",
        tool_parameters=ANALYZE_INPUT_TOOL["function"]["parameters"],
    )

    if result:
        return {
            "collected_info": CollectedInfo(
                domain=result.get("domain", ""),
                target_users=result.get("target_users", []),
                core_features=result.get("core_features", []),
                constraints=result.get("constraints", []),
                existing_context=result.get("existing_context", []),
            ),
            "current_step": "questions",
        }
    return {"error": "Failed to analyze input", "current_step": "analyze"}


def generate_questions(llm: LLM, state: RequirementState) -> dict[str, Any]:
    """Generate clarification questions."""
    if not state.collected_info:
        return {"error": "No collected info", "current_step": "analyze"}

    system = """你是一位需求分析师。根据已收集的信息，生成澄清问题来完善需求理解。
问题应该聚焦于：
- 不明确的功能细节
- 技术选型
- 优先级确认
- 边界条件

请使用 generate_questions 工具返回问题列表。"""

    context = f"""已收集信息:
- 领域: {state.collected_info.domain}
- 目标用户: {', '.join(state.collected_info.target_users)}
- 核心功能: {', '.join(state.collected_info.core_features)}
- 约束: {', '.join(state.collected_info.constraints)}
"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=context,
        tool_name="generate_questions",
        tool_description="Generate clarifying questions",
        tool_parameters=GENERATE_QUESTIONS_TOOL["function"]["parameters"],
    )

    if result and "questions" in result:
        questions = [
            Clarification(question=q.get("question", ""), reason=q.get("reason", ""))
            for q in result["questions"]
        ]
        return {"clarifications": questions, "current_step": "proposal"}
    return {"clarifications": [], "current_step": "proposal"}


def generate_proposal_node(llm: LLM, state: RequirementState) -> dict[str, Any]:
    """Generate the final proposal."""
    if not state.collected_info:
        return {"error": "No collected info", "current_step": "analyze"}

    system = """你是一位资深软件架构师。根据收集的需求信息，生成 OpenSpec proposal。
proposal 应包含：
- 清晰的背景和问题描述
- 明确的目标和非目标
- 按优先级排序的能力
- 风险评估和缓解措施
- 合理的时间线

请使用 generate_proposal_data 工具返回结构化数据。"""

    # Build context including answers
    answers_text = ""
    if state.clarifications:
        answers_text = "\n\n澄清问答:\n"
        for c in state.clarifications:
            answers_text += f"- Q: {c.question}\n"
            if c.answer:
                answers_text += f"  A: {c.answer}\n"

    context = f"""项目名称: {state.project_name}

已收集信息:
- 领域: {state.collected_info.domain}
- 目标用户: {', '.join(state.collected_info.target_users)}
- 核心功能: {', '.join(state.collected_info.core_features)}
- 约束: {', '.join(state.collected_info.constraints)}
- 现有上下文: {', '.join(state.collected_info.existing_context)}
{answers_text}
"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=context,
        tool_name="generate_proposal_data",
        tool_description="Generate OpenSpec proposal data",
        tool_parameters=GENERATE_PROPOSAL_TOOL["function"]["parameters"],
    )

    if result:
        # Convert to typed objects
        capabilities = [
            Capability(
                name=c.get("name", ""),
                description=c.get("description", ""),
                priority=Priority(c.get("priority", "should")),
            )
            for c in result.get("capabilities", [])
        ]
        risks = [
            Risk(
                description=r.get("description", ""),
                severity=Severity(r.get("severity", "medium")),
                mitigation=r.get("mitigation", ""),
            )
            for r in result.get("risks", [])
        ]

        inp = ProposalInput(
            projectName=result.get("project_name", state.project_name),
            background=result.get("background", ""),
            problem=result.get("problem", ""),
            goals=result.get("goals", []),
            nonGoals=result.get("non_goals", []),
            capabilities=capabilities,
            impactedAreas=result.get("impacted_areas", []),
            risks=risks,
            dependencies=result.get("dependencies", []),
            timeline=result.get("timeline", ""),
        )

        proposal = generate_proposal(inp)
        md = render_proposal_markdown(proposal)

        return {
            "proposal": proposal,
            "proposal_markdown": md,
            "current_step": "done",
        }

    return {"error": "Failed to generate proposal", "current_step": "proposal"}


# ---------------------------------------------------------------------------
# Workflow Runner
# ---------------------------------------------------------------------------


def run_requirement_workflow(
    llm: LLM,
    user_input: str,
    project_name: str,
    clarifications: list[Clarification] | None = None,
) -> RequirementState:
    """Run the complete requirement workflow."""
    state = RequirementState(
        user_input=user_input,
        project_name=project_name,
        clarifications=clarifications or [],
    )

    # Step 1: Analyze input
    updates = analyze_input(llm, state)
    for k, v in updates.items():
        setattr(state, k, v)

    if state.error or state.current_step != "questions":
        return state

    # Step 2: Generate questions (if no answers provided)
    if not state.clarifications or all(c.answer is None for c in state.clarifications):
        updates = generate_questions(llm, state)
        for k, v in updates.items():
            setattr(state, k, v)

    if state.error or state.current_step != "proposal":
        return state

    # Step 3: Generate proposal
    updates = generate_proposal_node(llm, state)
    for k, v in updates.items():
        setattr(state, k, v)

    return state
