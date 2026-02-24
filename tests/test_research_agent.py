"""Tests for research_agent.py — validation, logging, and graceful degradation."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from toyshop.research_agent import (
    ResearchPlan,
    default_research_plan,
    generate_kickoff_plan,
    try_gpt_researcher_summary,
    validate_research_plan,
)


# ---------------------------------------------------------------------------
# validate_research_plan
# ---------------------------------------------------------------------------

class TestValidateResearchPlan:
    def test_valid_plan(self):
        plan = default_research_plan("build a calculator")
        warnings = validate_research_plan(plan)
        assert warnings == []

    def test_empty_mvp_option(self):
        plan = ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build something",
            mvp_option="",
            sota_option="full version",
            mvp_scope=["core"],
        )
        warnings = validate_research_plan(plan)
        assert any("mvp_option" in w for w in warnings)

    def test_empty_sota_option(self):
        plan = ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build something",
            mvp_option="minimal version",
            sota_option="",
            mvp_scope=["core"],
        )
        warnings = validate_research_plan(plan)
        assert any("sota_option" in w for w in warnings)

    def test_empty_problem_statement(self):
        plan = ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="   ",
            mvp_option="minimal",
            sota_option="full",
            mvp_scope=["core"],
        )
        warnings = validate_research_plan(plan)
        assert any("problem_statement" in w for w in warnings)

    def test_empty_mvp_scope(self):
        plan = ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="build something",
            mvp_option="minimal",
            sota_option="full",
            mvp_scope=[],
        )
        warnings = validate_research_plan(plan)
        assert any("mvp_scope" in w for w in warnings)

    def test_multiple_warnings(self):
        plan = ResearchPlan(
            trigger_type="kickoff_mvp_sota",
            problem_statement="",
            mvp_option="",
            sota_option="",
            mvp_scope=[],
        )
        warnings = validate_research_plan(plan)
        assert len(warnings) == 4


# ---------------------------------------------------------------------------
# try_gpt_researcher_summary
# ---------------------------------------------------------------------------

class TestTryGptResearcherSummary:
    def test_disabled_returns_empty(self):
        summary, sources = try_gpt_researcher_summary("test query", enabled=False)
        assert summary == ""
        assert sources == []

    def test_import_failure_returns_empty(self, monkeypatch):
        """When gpt_researcher is not installed, returns empty gracefully."""
        # Ensure the module is not importable
        monkeypatch.setitem(sys.modules, "gpt_researcher", None)
        summary, sources = try_gpt_researcher_summary("test query")
        assert summary == ""
        assert sources == []

    def test_truncates_long_summary(self, monkeypatch):
        """Summary > 4000 chars gets truncated."""
        import types

        # Create a fake gpt_researcher module
        fake_module = types.ModuleType("gpt_researcher")

        class FakeResearcher:
            def __init__(self, **kwargs):
                pass

            async def quick_search(self, query, aggregated_summary=True):
                if aggregated_summary:
                    return "x" * 5000
                return []

        fake_module.GPTResearcher = FakeResearcher
        monkeypatch.setitem(sys.modules, "gpt_researcher", fake_module)

        summary, sources = try_gpt_researcher_summary("test query", timebox_minutes=1)
        assert len(summary) < 5000
        assert summary.endswith("(truncated)")


# ---------------------------------------------------------------------------
# generate_kickoff_plan
# ---------------------------------------------------------------------------

class TestGenerateKickoffPlan:
    def test_falls_back_on_llm_failure(self, monkeypatch):
        """When LLM raises, falls back to default plan."""
        monkeypatch.setattr(
            "toyshop.research_agent.try_gpt_researcher_summary",
            lambda *a, **kw: ("", []),
        )
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(side_effect=RuntimeError("LLM down")),
        )
        mock_llm = MagicMock()
        plan = generate_kickoff_plan(user_input="build a calculator", llm=mock_llm)
        assert plan.mvp_option  # default plan has content
        assert plan.sota_option

    def test_falls_back_on_invalid_llm_result(self, monkeypatch):
        """When LLM returns empty mvp_option, falls back to default."""
        monkeypatch.setattr(
            "toyshop.research_agent.try_gpt_researcher_summary",
            lambda *a, **kw: ("", []),
        )
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(return_value={
                "problem_statement": "build a calculator",
                "mvp_option": "",  # invalid
                "sota_option": "full version",
                "mvp_scope": ["core"],
                "tradeoffs": [],
                "adoption_plan": [],
            }),
        )
        mock_llm = MagicMock()
        plan = generate_kickoff_plan(user_input="build a calculator", llm=mock_llm)
        # Should have fallen back to default
        assert "smallest end-to-end" in plan.mvp_option

    def test_merges_external_sources(self, monkeypatch):
        """External sources are merged into the plan."""
        monkeypatch.setattr(
            "toyshop.research_agent.try_gpt_researcher_summary",
            lambda *a, **kw: ("some research", ["https://example.com"]),
        )
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(return_value={
                "problem_statement": "build a calculator",
                "mvp_option": "basic calc",
                "sota_option": "advanced calc",
                "mvp_scope": ["core arithmetic"],
                "tradeoffs": ["speed vs features"],
                "adoption_plan": ["step1"],
            }),
        )
        mock_llm = MagicMock()
        plan = generate_kickoff_plan(user_input="build a calculator", llm=mock_llm)
        assert plan.sources == ["https://example.com"]
        assert plan.external_summary == "some research"

    def test_valid_llm_result_accepted(self, monkeypatch):
        """When LLM returns valid plan, it's used directly."""
        monkeypatch.setattr(
            "toyshop.research_agent.try_gpt_researcher_summary",
            lambda *a, **kw: ("", []),
        )
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(return_value={
                "problem_statement": "build a calculator",
                "mvp_option": "basic four operations",
                "sota_option": "scientific calculator with graphing",
                "mvp_scope": ["add", "subtract", "multiply", "divide"],
                "tradeoffs": ["simplicity vs completeness"],
                "adoption_plan": ["mvp", "sota"],
            }),
        )
        mock_llm = MagicMock()
        plan = generate_kickoff_plan(user_input="build a calculator", llm=mock_llm)
        assert plan.mvp_option == "basic four operations"
        assert plan.sota_option == "scientific calculator with graphing"
        assert len(plan.mvp_scope) == 4


# ---------------------------------------------------------------------------
# Stage 2: ResearchSpec, SOTA criteria, MVP extraction
# ---------------------------------------------------------------------------

from toyshop.research_agent import (
    ResearchSpec,
    build_research_spec,
    extract_mvp_from_sota,
    generate_sota_criteria,
)


class TestGenerateSotaCriteria:
    def test_returns_criteria_from_llm(self, monkeypatch):
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(return_value={
                "criteria": [
                    "Use connection pooling for database access",
                    "Implement retry with exponential backoff",
                    "Achieve 90% test coverage",
                ],
            }),
        )
        criteria = generate_sota_criteria("build a web API", "", MagicMock())
        assert len(criteria) == 3
        assert "connection pooling" in criteria[0]

    def test_falls_back_on_llm_failure(self, monkeypatch):
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(side_effect=RuntimeError("LLM down")),
        )
        criteria = generate_sota_criteria("build a web API", "", MagicMock())
        assert len(criteria) >= 1  # fallback defaults

    def test_falls_back_on_empty_result(self, monkeypatch):
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(return_value={"criteria": []}),
        )
        criteria = generate_sota_criteria("build a web API", "", MagicMock())
        assert len(criteria) >= 1  # fallback defaults


class TestExtractMvpFromSota:
    def test_classifies_criteria(self, monkeypatch):
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(return_value={
                "mvp_essential": ["basic CRUD operations", "input validation"],
                "sota_only": ["caching layer", "rate limiting"],
            }),
        )
        mvp, deferred = extract_mvp_from_sota(
            ["basic CRUD operations", "input validation", "caching layer", "rate limiting"],
            "build a web API",
            MagicMock(),
        )
        assert len(mvp) == 2
        assert len(deferred) == 2
        assert "CRUD" in mvp[0]

    def test_falls_back_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            "toyshop.research_agent.chat_with_tool",
            MagicMock(side_effect=RuntimeError("fail")),
        )
        criteria = ["a", "b", "c", "d"]
        mvp, deferred = extract_mvp_from_sota(criteria, "test", MagicMock())
        assert len(mvp) >= 1
        assert len(deferred) >= 1
        assert set(mvp + deferred) == set(criteria)


class TestBuildResearchSpec:
    def test_preserves_original_requirement(self, monkeypatch):
        # Mock both LLM calls
        call_count = [0]

        def fake_chat_with_tool(**kwargs):
            call_count[0] += 1
            if kwargs["tool_name"] == "generate_sota_criteria":
                return {"criteria": ["criterion A", "criterion B"]}
            if kwargs["tool_name"] == "classify_criteria":
                return {"mvp_essential": ["criterion A"], "sota_only": ["criterion B"]}
            return None

        monkeypatch.setattr("toyshop.research_agent.chat_with_tool", fake_chat_with_tool)

        plan = default_research_plan("Build a REST API for user management")
        spec = build_research_spec("Build a REST API for user management", plan, MagicMock())

        assert spec.original_requirement == "Build a REST API for user management"
        assert "criterion A" in spec.mvp_boundaries
        assert "criterion B" in spec.deferred_to_sota
        assert len(spec.acceptance_criteria) >= 1


# ---------------------------------------------------------------------------
# Stage 2: _build_structured_stage_requirement
# ---------------------------------------------------------------------------

from toyshop.pm import _build_structured_stage_requirement


class TestBuildStructuredStageRequirement:
    @pytest.fixture
    def sample_spec(self):
        return ResearchSpec(
            original_requirement="Build a calculator app",
            sota_criteria=["Use expression parser", "Support variables", "History tracking"],
            mvp_boundaries=["Basic four operations", "CLI interface"],
            deferred_to_sota=["Support variables", "History tracking"],
            acceptance_criteria=["Verify: Basic four operations", "Verify: CLI interface"],
            architecture_constraints=["Research context: use shunting-yard algorithm"],
            risk_items=["Parser complexity"],
        )

    def test_mvp_anchors_user_input_first(self, sample_spec):
        result = _build_structured_stage_requirement("Build a calculator app", sample_spec, "mvp")
        lines = result.split("\n")
        # Original requirement must appear before any stage content
        req_idx = next(i for i, l in enumerate(lines) if "不可偏离" in l)
        mvp_idx = next(i for i, l in enumerate(lines) if "MVP" in l and "阶段目标" in l)
        assert req_idx < mvp_idx

    def test_mvp_includes_boundaries_and_deferred(self, sample_spec):
        result = _build_structured_stage_requirement("Build a calculator app", sample_spec, "mvp")
        assert "Basic four operations" in result
        assert "CLI interface" in result
        assert "Support variables" in result  # in out-of-scope section
        assert "不要实现" in result

    def test_sota_includes_all_criteria(self, sample_spec):
        result = _build_structured_stage_requirement("Build a calculator app", sample_spec, "sota")
        assert "Use expression parser" in result
        assert "Support variables" in result
        assert "History tracking" in result
        assert "SOTA 标准" in result

    def test_original_requirement_verbatim(self, sample_spec):
        result = _build_structured_stage_requirement("Build a calculator app", sample_spec, "mvp")
        assert "Build a calculator app" in result

