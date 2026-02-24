"""Tests for reference source configuration and scanner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from toyshop.reference import (
    CodeSnippet,
    ReferenceConfig,
    ReferenceSource,
    ScanResult,
    load_reference_config,
    save_reference_config,
    scan_source_grep,
    score_snippets,
    scan_references,
    scan_result_to_dict,
    scan_result_from_dict,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


SAMPLE_TOML = """\
project_name = "testproject"
project_type = "python"

[[sources]]
id = "ref1"
name = "Reference One"
source_type = "logic"
path = "/tmp/ref1"
language = "python"
tags = ["combat", "ai"]
description = "Test reference"

[[sources]]
id = "ref2"
name = "Reference Two"
source_type = "mechanism"
path = "/tmp/ref2"
language = "java"
tags = ["mixin", "registry"]
analyzer = "modfactory"
"""


def _make_source_dir(tmp_path: Path) -> Path:
    """Create a small source directory for grep testing."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "combat.py").write_text(
        "class CombatSystem:\n"
        "    def calculate_damage(self, attacker, defender):\n"
        "        base = attacker.strength - defender.armor\n"
        "        return max(0, base)\n"
        "\n"
        "    def resolve_attack(self, attacker, defender):\n"
        "        damage = self.calculate_damage(attacker, defender)\n"
        "        defender.hp -= damage\n"
        "        return damage\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        "def clamp(value, lo, hi):\n"
        "    return max(lo, min(hi, value))\n",
        encoding="utf-8",
    )
    (src / "readme.txt").write_text("Not a Python file\ncombat info here\n")
    return src


# ---------------------------------------------------------------------------
# TestReferenceConfig
# ---------------------------------------------------------------------------


class TestReferenceConfig:
    def test_load_valid_toml(self, tmp_path):
        toml_path = tmp_path / "refs.toml"
        toml_path.write_text(SAMPLE_TOML, encoding="utf-8")
        config = load_reference_config(toml_path)
        assert config.project_name == "testproject"
        assert config.project_type == "python"
        assert len(config.sources) == 2
        assert config.sources[0].id == "ref1"
        assert config.sources[0].source_type == "logic"
        assert config.sources[0].tags == ["combat", "ai"]
        assert config.sources[1].analyzer == "modfactory"

    def test_load_missing_file(self, tmp_path):
        config = load_reference_config(tmp_path / "nonexistent.toml")
        assert config.project_name == ""
        assert config.sources == []

    def test_save_and_reload(self, tmp_path):
        config = ReferenceConfig(
            project_name="myproj",
            project_type="java",
            sources=[
                ReferenceSource(
                    id="s1", name="Source 1", source_type="logic",
                    path="/tmp/s1", language="java", tags=["tag1"],
                ),
            ],
        )
        path = tmp_path / "out.toml"
        save_reference_config(config, path)
        reloaded = load_reference_config(path)
        assert reloaded.project_name == "myproj"
        assert len(reloaded.sources) == 1
        assert reloaded.sources[0].id == "s1"
        assert reloaded.sources[0].tags == ["tag1"]

    def test_load_minimal_toml(self, tmp_path):
        toml_path = tmp_path / "min.toml"
        toml_path.write_text('project_name = "x"\nproject_type = "y"\n')
        config = load_reference_config(toml_path)
        assert config.project_name == "x"
        assert config.sources == []


# ---------------------------------------------------------------------------
# TestGrepScanner
# ---------------------------------------------------------------------------


class TestGrepScanner:
    def test_scan_finds_keyword_matches(self, tmp_path):
        src = _make_source_dir(tmp_path)
        source = ReferenceSource(
            id="test", name="Test", source_type="logic",
            path=str(src), language="python",
        )
        snippets = scan_source_grep(source, ["combat"])
        assert len(snippets) >= 1
        assert any("CombatSystem" in s.content for s in snippets)

    def test_scan_respects_language_filter(self, tmp_path):
        src = _make_source_dir(tmp_path)
        source = ReferenceSource(
            id="test", name="Test", source_type="logic",
            path=str(src), language="python",
        )
        # "combat" appears in readme.txt too, but should be filtered
        snippets = scan_source_grep(source, ["combat"])
        for s in snippets:
            assert s.file_path.endswith(".py")

    def test_scan_respects_max_snippets(self, tmp_path):
        src = _make_source_dir(tmp_path)
        source = ReferenceSource(
            id="test", name="Test", source_type="logic",
            path=str(src), language="python",
        )
        snippets = scan_source_grep(source, ["def", "class", "return"], max_snippets=2)
        assert len(snippets) <= 2

    def test_scan_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        source = ReferenceSource(
            id="test", name="Test", source_type="logic",
            path=str(empty), language="python",
        )
        snippets = scan_source_grep(source, ["anything"])
        assert snippets == []

    def test_scan_nonexistent_dir(self, tmp_path):
        source = ReferenceSource(
            id="test", name="Test", source_type="logic",
            path=str(tmp_path / "nope"), language="python",
        )
        snippets = scan_source_grep(source, ["anything"])
        assert snippets == []

    def test_snippet_has_context(self, tmp_path):
        src = _make_source_dir(tmp_path)
        source = ReferenceSource(
            id="test", name="Test", source_type="logic",
            path=str(src), language="python",
        )
        snippets = scan_source_grep(source, ["calculate_damage"])
        assert len(snippets) >= 1
        # Should include surrounding lines
        assert "CombatSystem" in snippets[0].content or "base" in snippets[0].content


# ---------------------------------------------------------------------------
# TestScoreSnippets
# ---------------------------------------------------------------------------


class TestScoreSnippets:
    def test_score_with_mock_llm(self):
        snippets = [
            CodeSnippet("s1", "a.py", 1, 5, "def attack(): pass", "python"),
            CodeSnippet("s1", "b.py", 1, 5, "def heal(): pass", "python"),
        ]

        def mock_chat(llm, system, user, name, desc, params):
            return {
                "scores": [
                    {"index": 0, "score": 0.9, "reason": "Direct combat code"},
                    {"index": 1, "score": 0.2, "reason": "Healing, not combat"},
                ],
            }

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            scored = score_snippets("combat system", snippets, MagicMock())

        assert len(scored) == 2
        assert scored[0][1] == 0.9  # highest first
        assert scored[1][1] == 0.2

    def test_score_fallback_on_none(self):
        snippets = [
            CodeSnippet("s1", "a.py", 1, 5, "code", "python"),
        ]

        def mock_chat(llm, system, user, name, desc, params):
            return None

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            scored = score_snippets("test", snippets, MagicMock())

        assert len(scored) == 1
        assert scored[0][1] == 0.5  # fallback score

    def test_score_empty_snippets(self):
        scored = score_snippets("test", [], MagicMock())
        assert scored == []


# ---------------------------------------------------------------------------
# TestScanReferences
# ---------------------------------------------------------------------------


class TestScanReferences:
    def test_scan_end_to_end(self, tmp_path):
        src = _make_source_dir(tmp_path)
        config = ReferenceConfig(
            project_name="test",
            project_type="python",
            sources=[
                ReferenceSource(
                    id="testsrc", name="Test Source", source_type="logic",
                    path=str(src), language="python", tags=["combat"],
                ),
            ],
        )

        def mock_chat(llm, system, user, name, desc, params):
            return {
                "scores": [
                    {"index": 0, "score": 0.8, "reason": "Relevant combat code"},
                ],
            }

        with patch("toyshop.llm.chat_with_tool", mock_chat):
            results = scan_references(
                "asp_1", "logic", ["combat"], config, MagicMock(),
            )

        assert len(results) >= 1
        assert results[0].source_id == "testsrc"
        assert results[0].relevance_score > 0

    def test_scan_no_matching_sources(self):
        config = ReferenceConfig(
            project_name="test", project_type="python", sources=[],
        )
        results = scan_references("asp_1", "logic", ["x"], config, MagicMock())
        assert results == []


# ---------------------------------------------------------------------------
# TestSerialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_roundtrip(self):
        result = ScanResult(
            aspect_id="asp_1",
            source_id="src_1",
            snippets=[
                CodeSnippet("src_1", "a.py", 1, 10, "code here", "python"),
            ],
            relevance_score=0.85,
            relevance_reason="Good match",
        )
        d = scan_result_to_dict(result)
        restored = scan_result_from_dict(d)
        assert restored.aspect_id == "asp_1"
        assert restored.relevance_score == 0.85
        assert len(restored.snippets) == 1
        assert restored.snippets[0].content == "code here"
