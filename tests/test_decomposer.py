"""Tests for requirement decomposer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from toyshop.decomposer import (
    DecompositionResult,
    RequirementAspect,
    decompose_requirement,
    match_aspects_to_sources,
    decomposition_to_dict,
    decomposition_from_dict,
    _infer_mc_category,
    _fallback_decomposition,
)
from toyshop.reference import ReferenceConfig, ReferenceSource


# ---------------------------------------------------------------------------
# TestDecomposeRequirement
# ---------------------------------------------------------------------------


class TestDecomposeRequirement:
    def test_decompose_mc_requirement(self):
        def mock_chat(llm, system, user, name, desc, params):
            return {
                "aspects": [
                    {
                        "id": "asp_1",
                        "title": "Ice Projectile Logic",
                        "description": "Projectile that applies slowness on hit",
                        "aspect_type": "logic",
                        "category": "server",
                        "keywords": ["projectile", "slowness", "damage", "entity"],
                        "priority": "must",
                    },
                    {
                        "id": "asp_2",
                        "title": "Bow Item Registration",
                        "description": "Register custom bow item with Fabric",
                        "aspect_type": "mechanism",
                        "category": "shared",
                        "keywords": ["item", "registry", "bow", "fabric"],
                        "priority": "must",
                    },
                    {
                        "id": "asp_3",
                        "title": "Ice Particle Effects",
                        "description": "Visual ice particles on projectile trail",
                        "aspect_type": "content",
                        "category": "client",
                        "keywords": ["particle", "render", "ice", "trail"],
                        "priority": "should",
                    },
                ],
                "rationale": "Split into server logic, shared registration, and client visuals",
            }

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            result = decompose_requirement(
                "创建一个寒冰弓mod，发射冰霜投射物",
                "java-minecraft",
                MagicMock(),
            )

        assert len(result.aspects) == 3
        assert result.aspects[0].category == "server"
        assert result.aspects[1].category == "shared"
        assert result.aspects[2].category == "client"
        assert result.aspects[0].aspect_type == "logic"
        assert result.aspects[1].aspect_type == "mechanism"

    def test_decompose_python_requirement(self):
        def mock_chat(llm, system, user, name, desc, params):
            return {
                "aspects": [
                    {
                        "id": "asp_1",
                        "title": "Reference Scanner",
                        "description": "Grep-based code search",
                        "aspect_type": "logic",
                        "keywords": ["grep", "search", "code"],
                    },
                ],
                "rationale": "Single aspect",
            }

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            result = decompose_requirement(
                "Add reference source scanning",
                "python",
                MagicMock(),
            )

        assert len(result.aspects) == 1
        # Non-MC project: category stays as-is (default "general")
        assert result.aspects[0].category == "general"

    def test_decompose_fallback_on_none(self):
        def mock_chat(llm, system, user, name, desc, params):
            return None

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            result = decompose_requirement(
                "Create a frost bow mod",
                "java-minecraft",
                MagicMock(),
            )

        assert len(result.aspects) == 1
        assert result.aspects[0].title == "Core Implementation"
        assert "Fallback" in result.rationale

    def test_mc_category_inference(self):
        def mock_chat(llm, system, user, name, desc, params):
            return {
                "aspects": [
                    {
                        "id": "asp_1",
                        "title": "Render System",
                        "description": "Custom block model rendering",
                        "aspect_type": "mechanism",
                        "category": "general",  # LLM didn't categorize
                        "keywords": ["render", "model", "texture"],
                    },
                ],
                "rationale": "Test",
            }

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            result = decompose_requirement(
                "Add custom block rendering",
                "java-minecraft",
                MagicMock(),
            )

        # Should auto-infer "client" from render/model/texture keywords
        assert result.aspects[0].category == "client"


# ---------------------------------------------------------------------------
# TestInferMcCategory
# ---------------------------------------------------------------------------


class TestInferMcCategory:
    def test_server_keywords(self):
        assert _infer_mc_category(["command", "event"], "game logic") == "server"

    def test_client_keywords(self):
        assert _infer_mc_category(["render", "texture"], "visual effects") == "client"

    def test_shared_keywords(self):
        assert _infer_mc_category(["block", "item"], "block registration") == "shared"

    def test_no_match_defaults_shared(self):
        assert _infer_mc_category(["xyz"], "unknown") == "shared"


# ---------------------------------------------------------------------------
# TestFallbackDecomposition
# ---------------------------------------------------------------------------


class TestFallbackDecomposition:
    def test_produces_single_aspect(self):
        result = _fallback_decomposition("Create a frost bow", "java-minecraft")
        assert len(result.aspects) == 1
        assert result.aspects[0].id == "asp_1"
        assert "frost" in result.aspects[0].keywords

    def test_keywords_from_requirement(self):
        result = _fallback_decomposition("Add custom weapons with elemental damage", "python")
        assert len(result.aspects[0].keywords) > 0


# ---------------------------------------------------------------------------
# TestMatchAspectsToSources
# ---------------------------------------------------------------------------


class TestMatchAspectsToSources:
    def test_match_calls_scan_for_each_aspect(self):
        decomp = DecompositionResult(
            original_requirement="test",
            project_type="python",
            aspects=[
                RequirementAspect("a1", "A1", "desc", "logic", "general", ["kw1"]),
                RequirementAspect("a2", "A2", "desc", "mechanism", "general", ["kw2"]),
            ],
        )
        config = ReferenceConfig("test", "python", [])

        with patch("toyshop.decomposer.scan_references", return_value=[]) as mock_scan:
            results = match_aspects_to_sources(decomp, config, MagicMock())

        assert "a1" in results
        assert "a2" in results
        assert mock_scan.call_count == 2


# ---------------------------------------------------------------------------
# TestSerialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_roundtrip(self):
        result = DecompositionResult(
            original_requirement="test req",
            project_type="python",
            aspects=[
                RequirementAspect("a1", "Title", "Desc", "logic", "general", ["kw"], "must"),
            ],
            rationale="test rationale",
        )
        d = decomposition_to_dict(result)
        restored = decomposition_from_dict(d)
        assert restored.original_requirement == "test req"
        assert len(restored.aspects) == 1
        assert restored.aspects[0].id == "a1"
        assert restored.aspects[0].keywords == ["kw"]
