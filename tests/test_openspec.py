"""Tests for OpenSpec types, generator, parser, validator."""

import pytest

from toyshop.openspec.types import (
    OpenSpecProposal,
    OpenSpecDesign,
    OpenSpecTasks,
    OpenSpecSpec,
    ProposalInput,
    Priority,
    Severity,
    TaskStatus,
    Capability,
    Risk,
    Task,
    Scenario,
)
from toyshop.openspec.generator import (
    generate_proposal,
    generate_design,
    generate_tasks,
    generate_spec,
    render_proposal_markdown,
    render_design_markdown,
    render_tasks_markdown,
    render_spec_markdown,
)
from toyshop.openspec.parser import (
    parse_proposal,
    parse_design,
    parse_tasks,
    parse_spec,
)
from toyshop.openspec.validator import (
    validate_proposal,
    validate_design,
    validate_tasks,
    validate_spec,
)


class TestProposal:
    def test_generate_and_validate(self):
        inp = ProposalInput(
            projectName="TestApp",
            background="Need a simple app",
            problem="No app exists",
            goals=["Build app", "Deploy app"],
            nonGoals=["Mobile version"],
            capabilities=[
                Capability(name="Core", description="Core feature", priority=Priority.MUST)
            ],
            impactedAreas=["backend"],
            risks=[Risk(description="API changes", severity=Severity.MEDIUM, mitigation="Versioning")],
            dependencies=["external-api"],
            timeline="2 weeks",
        )
        proposal = generate_proposal(inp)
        assert proposal.project_name == "TestApp"
        assert len(proposal.goals) == 2
        assert len(proposal.capabilities) == 1

        result = validate_proposal(proposal)
        assert result.valid
        assert len(result.errors) == 0

    def test_render_markdown(self):
        inp = ProposalInput(
            projectName="MyProject",
            background="Background text",
            problem="Problem text",
            goals=["Goal A"],
        )
        proposal = generate_proposal(inp)
        md = render_proposal_markdown(proposal)
        assert "# MyProject" in md
        assert "Background text" in md
        assert "Problem text" in md

    def test_parse_markdown(self):
        md = """# TestProject

Format: OpenSpec v1.0 Proposal

## Why

### Background
This is the background.

### Problem
This is the problem.

### Goals
- Goal 1
- Goal 2

## What Changes

### Impacted Areas
- Area 1

## Impact

### Dependencies
- Dep 1

### Timeline
1 week
"""
        proposal = parse_proposal(md)
        assert proposal is not None
        assert proposal.project_name == "TestProject"
        assert "background" in proposal.background.lower()
        assert len(proposal.goals) == 2
        assert "Area 1" in proposal.impacted_areas


class TestDesign:
    def test_generate_and_validate(self):
        from toyshop.openspec.types import DesignInput, Goal, ModuleDefinition

        inp = DesignInput(
            requirement="Build a REST API",
            constraints=["Use PostgreSQL"],
            goals=[Goal(id="G1", description="Performance", metrics=["< 100ms"])],
            modules=[
                ModuleDefinition(
                    id="api",
                    name="API",
                    description="REST API module",
                    responsibilities=["Handle requests"],
                    dependencies=[],
                    filePath="src/api/index.ts",
                )
            ],
        )
        design = generate_design(inp)
        assert design.requirement == "Build a REST API"
        assert len(design.modules) == 1

        result = validate_design(design)
        assert result.valid


class TestTasks:
    def test_generate_and_validate(self):
        from toyshop.openspec.types import TasksInput
        inp = TasksInput(tasks=[
            Task(id="1", title="Setup", description="Initial setup", status=TaskStatus.PENDING, dependencies=[]),
            Task(id="1.1", title="Install deps", description="", status=TaskStatus.PENDING, dependencies=["1"]),
        ])
        tasks = generate_tasks(inp)
        assert len(tasks.tasks) == 2

        result = validate_tasks(tasks)
        assert result.valid


class TestSpec:
    def test_generate_and_validate(self):
        from toyshop.openspec.types import SpecInput
        inp = SpecInput(scenarios=[
            Scenario(
                id="S1",
                name="User login",
                given="user is on login page",
                when="user enters credentials",
                then="user is redirected to dashboard",
            )
        ])
        spec = generate_spec(inp)
        assert len(spec.scenarios) == 1

        result = validate_spec(spec)
        assert result.valid


class TestRoundTrip:
    def test_proposal_roundtrip(self):
        """Generate markdown, parse it back, validate."""
        inp = ProposalInput(
            projectName="RoundTrip",
            background="BG",
            problem="PB",
            goals=["G1"],
        )
        proposal = generate_proposal(inp)
        md = render_proposal_markdown(proposal)
        parsed = parse_proposal(md)
        assert parsed is not None
        assert parsed.project_name == "RoundTrip"
