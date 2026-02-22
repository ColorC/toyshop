"""OpenSpec document generators.

Convert structured data to OpenSpec documents and render to markdown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from toyshop.openspec.types import (
    OpenSpecProposal,
    OpenSpecDesign,
    OpenSpecTasks,
    OpenSpecSpec,
    OpenSpecBundle,
    ProposalInput,
    DesignInput,
    TasksInput,
    SpecInput,
    Priority,
    TaskStatus,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------


def generate_proposal(input: ProposalInput) -> OpenSpecProposal:
    """Generate an OpenSpecProposal from input data."""
    return OpenSpecProposal(
        projectName=input.project_name,
        background=input.background,
        problem=input.problem,
        goals=input.goals,
        nonGoals=input.non_goals,
        capabilities=input.capabilities,
        impactedAreas=input.impacted_areas,
        risks=input.risks,
        dependencies=input.dependencies,
        timeline=input.timeline,
    )


def render_proposal_markdown(proposal: OpenSpecProposal) -> str:
    """Render an OpenSpecProposal to markdown."""
    sections = []

    # Header
    sections.append(f"# {proposal.project_name}")
    sections.append("")
    sections.append(f"Format: {proposal.format}")
    sections.append("")

    # Why
    sections.append("## Why")
    sections.append("")
    sections.append("### Background")
    sections.append(proposal.background)
    sections.append("")
    sections.append("### Problem")
    sections.append(proposal.problem)
    sections.append("")
    sections.append("### Goals")
    for goal in proposal.goals:
        sections.append(f"- {goal}")
    sections.append("")
    sections.append("### Non-Goals")
    if proposal.non_goals:
        for ng in proposal.non_goals:
            sections.append(f"- {ng}")
    else:
        sections.append("_None defined_")
    sections.append("")

    # What Changes
    sections.append("## What Changes")
    sections.append("")
    sections.append("### Capabilities")
    priority_emoji = {
        Priority.MUST: "🔴",
        Priority.SHOULD: "🟡",
        Priority.COULD: "🟢",
        Priority.WONT: "⚪",
    }
    for cap in proposal.capabilities:
        emoji = priority_emoji.get(cap.priority, "⚪")
        sections.append(f"- **{cap.name}** {emoji} ({cap.priority.value})")
        sections.append(f"  {cap.description}")
    sections.append("")
    sections.append("### Impacted Areas")
    for area in proposal.impacted_areas:
        sections.append(f"- {area}")
    sections.append("")

    # Impact
    sections.append("## Impact")
    sections.append("")
    sections.append("### Risks")
    if proposal.risks:
        for risk in proposal.risks:
            sections.append(f"- **{risk.severity.value.upper()}**: {risk.description}")
            sections.append(f"  - Mitigation: {risk.mitigation}")
    else:
        sections.append("_No risks identified_")
    sections.append("")
    sections.append("### Dependencies")
    if proposal.dependencies:
        for dep in proposal.dependencies:
            sections.append(f"- {dep}")
    else:
        sections.append("_No dependencies_")
    sections.append("")
    sections.append("### Timeline")
    sections.append(proposal.timeline or "_Not defined_")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Design
# ---------------------------------------------------------------------------


def generate_design(input: DesignInput) -> OpenSpecDesign:
    """Generate an OpenSpecDesign from input data."""
    return OpenSpecDesign(
        requirement=input.requirement,
        constraints=input.constraints,
        goals=input.goals,
        decisions=input.decisions,
        modules=input.modules,
        interfaces=input.interfaces,
        dataModels=input.data_models,
        apiEndpoints=input.api_endpoints,
        risks=input.risks,
        tradeoffs=input.tradeoffs,
    )


def render_design_markdown(design: OpenSpecDesign) -> str:
    """Render an OpenSpecDesign to markdown."""
    sections = []

    # Header
    sections.append("# Technical Design")
    sections.append("")
    sections.append(f"Format: {design.format}")
    sections.append("")

    # Context
    sections.append("## Context")
    sections.append("")
    sections.append("### Requirement")
    sections.append(design.requirement)
    sections.append("")
    sections.append("### Constraints")
    if design.constraints:
        for c in design.constraints:
            sections.append(f"- {c}")
    else:
        sections.append("_No constraints defined_")
    sections.append("")

    # Goals
    sections.append("## Goals")
    sections.append("")
    for goal in design.goals:
        sections.append(f"### {goal.id}: {goal.description}")
        if goal.metrics:
            sections.append("")
            sections.append("**Metrics:**")
            for m in goal.metrics:
                sections.append(f"- {m}")
        sections.append("")

    # Decisions
    sections.append("## Architecture Decisions")
    sections.append("")
    for decision in design.decisions:
        sections.append(f"### ADR-{decision.id}: {decision.title}")
        sections.append("")
        sections.append(f"**Context:** {decision.context}")
        sections.append("")
        sections.append(f"**Decision:** {decision.decision}")
        sections.append("")
        sections.append(f"**Consequences:** {decision.consequences}")
        if decision.alternatives:
            sections.append("")
            sections.append("**Alternatives Considered:**")
            for a in decision.alternatives:
                sections.append(f"- {a}")
        sections.append("")

    # Architecture
    sections.append("## Architecture")
    sections.append("")
    sections.append("### Modules")
    for mod in design.modules:
        sections.append(f"#### {mod.name} (`{mod.id}`)")
        sections.append(mod.description)
        sections.append("")
        sections.append(f"- **File:** `{mod.file_path}`")
        sections.append("- **Responsibilities:**")
        for r in mod.responsibilities:
            sections.append(f"  - {r}")
        if mod.dependencies:
            sections.append("- **Dependencies:**")
            for d in mod.dependencies:
                sections.append(f"  - `{d}`")
        sections.append("")

    sections.append("### Interfaces")
    for intf in design.interfaces:
        sections.append(f"#### {intf.name} (`{intf.id}`)")
        sections.append(f"- **Type:** {intf.type.value}")
        sections.append(f"- **Module:** `{intf.module_id}`")
        sections.append(f"- **Signature:** `{intf.signature}`")
        sections.append(f"- **Description:** {intf.description}")
        sections.append("")

    # Risks
    sections.append("## Risks & Tradeoffs")
    sections.append("")
    sections.append("### Risks")
    if design.risks:
        for risk in design.risks:
            sections.append(f"- **{risk.severity.value.upper()}**: {risk.description}")
    else:
        sections.append("_No risks identified_")
    sections.append("")
    sections.append("### Tradeoffs")
    if design.tradeoffs:
        for t in design.tradeoffs:
            sections.append(f"- **{t.aspect}**: Chose \"{t.choice}\" over \"{t.alternative}\"")
            sections.append(f"  - Rationale: {t.rationale}")
    else:
        sections.append("_No tradeoffs documented_")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def generate_tasks(input: TasksInput) -> OpenSpecTasks:
    """Generate an OpenSpecTasks from input data."""
    return OpenSpecTasks(tasks=input.tasks)


def render_tasks_markdown(tasks: OpenSpecTasks) -> str:
    """Render an OpenSpecTasks to markdown."""
    sections = []

    sections.append("# Tasks")
    sections.append("")
    sections.append(f"Format: {tasks.format}")
    sections.append("")

    # Group tasks by top-level ID
    top_level = [t for t in tasks.tasks if "." not in t.id]
    subtasks = [t for t in tasks.tasks if "." in t.id]

    status_emoji = {
        TaskStatus.PENDING: "⬜",
        TaskStatus.IN_PROGRESS: "🔄",
        TaskStatus.COMPLETED: "✅",
        TaskStatus.BLOCKED: "🚫",
    }

    for top in top_level:
        emoji = status_emoji.get(top.status, "⬜")
        sections.append(f"## {emoji} {top.id}. {top.title}")
        sections.append(top.description)
        sections.append("")

        # Find children
        children = [
            t for t in subtasks
            if t.id.startswith(f"{top.id}.") and len(t.id.split(".")) == 2
        ]

        if children:
            for child in children:
                child_emoji = status_emoji.get(child.status, "⬜")
                sections.append(f"- {child_emoji} **{child.id}** {child.title}")
                if child.assigned_module:
                    sections.append(f"  - Module: `{child.assigned_module}`")
            sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


def generate_spec(input: SpecInput) -> OpenSpecSpec:
    """Generate an OpenSpecSpec from input data."""
    return OpenSpecSpec(scenarios=input.scenarios)


def render_spec_markdown(spec: OpenSpecSpec) -> str:
    """Render an OpenSpecSpec to markdown."""
    sections = []

    sections.append("# Specification")
    sections.append("")
    sections.append(f"Format: {spec.format}")
    sections.append("")

    for scenario in spec.scenarios:
        sections.append(f"## Scenario: {scenario.name}")
        sections.append("")
        sections.append(f"**ID:** `{scenario.id}`")
        sections.append("")
        sections.append("```gherkin")
        sections.append(f"GIVEN {scenario.given}")
        sections.append(f"WHEN {scenario.when}")
        sections.append(f"THEN {scenario.then}")
        sections.append("```")
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


def render_bundle_markdown(bundle: OpenSpecBundle) -> dict[str, str]:
    """Render all OpenSpec documents to markdown strings."""
    return {
        "proposal": render_proposal_markdown(bundle.proposal),
        "design": render_design_markdown(bundle.design),
        "tasks": render_tasks_markdown(bundle.tasks),
        "spec": render_spec_markdown(bundle.spec),
    }
