"""Tests for decision engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from toyshop.decision_engine import (
    Decision,
    ProjectCandidate,
    analyze_existing_projects,
    decide_create_or_modify,
    decision_to_dict,
    decision_from_dict,
    _scan_project_summary,
)
from toyshop.decomposer import DecompositionResult, RequirementAspect


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mod_project(tmp_path: Path, name: str) -> Path:
    """Create a minimal Gradle mod project."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / "build.gradle").write_text(
        'plugins { id "fabric-loom" }\n'
        f'archivesBaseName = "{name}"\n'
        'version = "1.0.0"\n',
        encoding="utf-8",
    )
    src = proj / "src" / "main" / "java"
    src.mkdir(parents=True)
    (src / "Main.java").write_text(
        f"public class Main {{\n"
        f"    // {name} mod entry point\n"
        f"}}\n",
        encoding="utf-8",
    )
    resources = proj / "src" / "main" / "resources"
    resources.mkdir(parents=True)
    import json
    (resources / "fabric.mod.json").write_text(
        json.dumps({"id": name, "description": f"A {name} mod"}),
        encoding="utf-8",
    )
    return proj


def _make_decomposition(requirement: str = "test") -> DecompositionResult:
    return DecompositionResult(
        original_requirement=requirement,
        project_type="java-minecraft",
        aspects=[
            RequirementAspect("a1", "Combat", "Combat system", "logic", "server", ["combat"]),
            RequirementAspect("a2", "Items", "Custom items", "mechanism", "shared", ["item"]),
        ],
    )


# ---------------------------------------------------------------------------
# TestAnalyzeExistingProjects
# ---------------------------------------------------------------------------


class TestAnalyzeExistingProjects:
    def test_finds_gradle_projects(self, tmp_path):
        _make_mod_project(tmp_path, "rpgmod")
        _make_mod_project(tmp_path, "weapons")
        candidates = analyze_existing_projects(tmp_path)
        assert len(candidates) == 2
        names = {c.name for c in candidates}
        assert "rpgmod" in names
        assert "weapons" in names

    def test_skips_non_projects(self, tmp_path):
        (tmp_path / "random_dir").mkdir()
        (tmp_path / ".hidden").mkdir()
        _make_mod_project(tmp_path, "real_mod")
        candidates = analyze_existing_projects(tmp_path)
        assert len(candidates) == 1
        assert candidates[0].name == "real_mod"

    def test_empty_dir(self, tmp_path):
        candidates = analyze_existing_projects(tmp_path)
        assert candidates == []

    def test_nonexistent_dir(self, tmp_path):
        candidates = analyze_existing_projects(tmp_path / "nope")
        assert candidates == []

    def test_summary_includes_mod_info(self, tmp_path):
        _make_mod_project(tmp_path, "testmod")
        candidates = analyze_existing_projects(tmp_path)
        assert len(candidates) == 1
        assert "testmod" in candidates[0].summary


# ---------------------------------------------------------------------------
# TestScanProjectSummary
# ---------------------------------------------------------------------------


class TestScanProjectSummary:
    def test_gradle_project(self, tmp_path):
        proj = _make_mod_project(tmp_path, "mymod")
        summary = _scan_project_summary(proj)
        assert "build.gradle" in summary
        assert "Mod ID: mymod" in summary

    def test_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        summary = _scan_project_summary(empty)
        assert "Empty" in summary or "unrecognized" in summary


# ---------------------------------------------------------------------------
# TestDecideCreateOrModify
# ---------------------------------------------------------------------------


class TestDecideCreateOrModify:
    def test_create_when_no_candidates(self):
        decomp = _make_decomposition()
        decision = decide_create_or_modify(decomp, [], MagicMock())
        assert decision.action == "create"
        assert "No existing" in decision.rationale

    def test_modify_when_llm_says_modify(self):
        decomp = _make_decomposition("Add new weapons to rpgmod")
        candidates = [
            ProjectCandidate("rpgmod", "/tmp/rpgmod", "RPG mod with combat"),
        ]

        def mock_chat(llm, system, user, name, desc, params):
            return {
                "action": "modify",
                "target_project": "rpgmod",
                "rationale": "rpgmod already has combat system",
                "relevance_scores": [{"project": "rpgmod", "score": 0.8}],
            }

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            decision = decide_create_or_modify(decomp, candidates, MagicMock())

        assert decision.action == "modify"
        assert decision.target == "rpgmod"
        assert decision.candidates[0].relevance == 0.8

    def test_create_when_llm_says_create(self):
        decomp = _make_decomposition("Build a completely new magic system")
        candidates = [
            ProjectCandidate("rpgmod", "/tmp/rpgmod", "RPG mod"),
        ]

        def mock_chat(llm, system, user, name, desc, params):
            return {
                "action": "create",
                "rationale": "Magic system is fundamentally different",
                "relevance_scores": [{"project": "rpgmod", "score": 0.2}],
            }

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            decision = decide_create_or_modify(decomp, candidates, MagicMock())

        assert decision.action == "create"
        assert decision.target is None

    def test_fallback_on_llm_none(self):
        decomp = _make_decomposition()
        candidates = [ProjectCandidate("x", "/tmp/x", "summary")]

        def mock_chat(llm, system, user, name, desc, params):
            return None

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            decision = decide_create_or_modify(decomp, candidates, MagicMock())

        assert decision.action == "create"
        assert "unavailable" in decision.rationale.lower()


# ---------------------------------------------------------------------------
# TestSerialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_roundtrip(self):
        decision = Decision(
            action="modify",
            target="rpgmod",
            target_path="/tmp/rpgmod",
            rationale="Good overlap",
            candidates=[
                ProjectCandidate("rpgmod", "/tmp/rpgmod", "RPG mod", 0.8),
            ],
        )
        d = decision_to_dict(decision)
        restored = decision_from_dict(d)
        assert restored.action == "modify"
        assert restored.target == "rpgmod"
        assert len(restored.candidates) == 1
        assert restored.candidates[0].relevance == 0.8
