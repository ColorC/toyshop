"""Tests for reference-enriched PM pipeline (pm.py + pm_cli.py extensions)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from toyshop.pm import (
    create_batch,
    run_decompose,
    run_ref_scan,
    run_decide,
    run_enrich,
    run_batch_with_refs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mock_decompose_chat(llm, system, user, name, desc, params):
    """Mock LLM for decompose_requirement."""
    return {
        "aspects": [
            {
                "id": "asp_1",
                "title": "Combat Logic",
                "description": "Server-side combat system",
                "aspect_type": "logic",
                "category": "server",
                "keywords": ["combat", "damage", "entity"],
                "priority": "must",
            },
            {
                "id": "asp_2",
                "title": "Item Registration",
                "description": "Register custom items",
                "aspect_type": "mechanism",
                "category": "shared",
                "keywords": ["item", "registry"],
                "priority": "must",
            },
        ],
        "rationale": "Split into logic and mechanism",
    }


def _mock_score_chat(llm, system, user, name, desc, params):
    """Mock LLM for score_snippets."""
    return {
        "scores": [
            {"snippet_index": 0, "score": 0.8, "reason": "Relevant combat code"},
        ],
    }


def _mock_decide_chat(llm, system, user, name, desc, params):
    """Mock LLM for decide_create_or_modify."""
    return {
        "action": "create",
        "rationale": "No existing projects match",
        "relevance_scores": [],
    }


@pytest.fixture
def batch_dir(tmp_path):
    """Create a minimal batch for testing."""
    batch = create_batch(tmp_path, "test_project", "Create a combat mod", project_type="java-minecraft")
    return batch


# ---------------------------------------------------------------------------
# TestRunDecompose
# ---------------------------------------------------------------------------


class TestRunDecompose:
    def test_decompose_creates_json(self, batch_dir):
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            result = run_decompose(batch_dir, MagicMock())

        assert len(result.aspects) == 2
        decomp_path = batch_dir.batch_dir / "decomposition.json"
        assert decomp_path.exists()
        data = json.loads(decomp_path.read_text(encoding="utf-8"))
        assert len(data["aspects"]) == 2
        assert data["aspects"][0]["id"] == "asp_1"

    def test_decompose_with_context(self, batch_dir):
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            result = run_decompose(batch_dir, MagicMock(), context="Extra context")

        assert len(result.aspects) == 2


# ---------------------------------------------------------------------------
# TestRunRefScan
# ---------------------------------------------------------------------------


class TestRunRefScan:
    def test_ref_scan_requires_decomposition(self, batch_dir):
        with pytest.raises(FileNotFoundError, match="decomposition.json"):
            run_ref_scan(batch_dir, MagicMock())

    def test_ref_scan_creates_reports(self, batch_dir, tmp_path):
        # First decompose
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            run_decompose(batch_dir, MagicMock())

        # Create a minimal reference source directory
        ref_src = tmp_path / "ref_source"
        ref_src.mkdir()
        (ref_src / "combat.java").write_text(
            "public class Combat {\n    void damage() {}\n}\n",
            encoding="utf-8",
        )

        # Create reference config
        config_path = tmp_path / "refs.toml"
        config_path.write_text(
            f'project_name = "test"\n'
            f'project_type = "java-minecraft"\n\n'
            f'[[sources]]\n'
            f'id = "test_ref"\n'
            f'name = "Test Reference"\n'
            f'source_type = "logic"\n'
            f'path = "{ref_src}"\n'
            f'language = "java"\n'
            f'tags = ["combat"]\n',
            encoding="utf-8",
        )

        with patch("toyshop.llm.chat_with_tool", _mock_score_chat):
            reports = run_ref_scan(batch_dir, MagicMock(), ref_config_path=config_path)

        assert "asp_1" in reports or "asp_2" in reports
        reports_dir = batch_dir.batch_dir / "reference_reports"
        assert reports_dir.is_dir()

    def test_ref_scan_copies_config(self, batch_dir, tmp_path):
        # Decompose first
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            run_decompose(batch_dir, MagicMock())

        # Create config outside batch
        config_path = tmp_path / "external_refs.toml"
        config_path.write_text(
            'project_name = "test"\nproject_type = "python"\n',
            encoding="utf-8",
        )

        with patch("toyshop.llm.chat_with_tool", _mock_score_chat):
            run_ref_scan(batch_dir, MagicMock(), ref_config_path=config_path)

        # Config should be copied into batch
        assert (batch_dir.batch_dir / "references.toml").exists()


# ---------------------------------------------------------------------------
# TestRunDecide
# ---------------------------------------------------------------------------


class TestRunDecide:
    def test_decide_requires_decomposition(self, batch_dir):
        with pytest.raises(FileNotFoundError, match="decomposition.json"):
            run_decide(batch_dir, MagicMock())

    def test_decide_creates_json(self, batch_dir):
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            run_decompose(batch_dir, MagicMock())

        with patch("toyshop.llm.chat_with_tool", _mock_decide_chat):
            decision = run_decide(batch_dir, MagicMock())

        assert decision.action == "create"
        decision_path = batch_dir.batch_dir / "decision.json"
        assert decision_path.exists()
        data = json.loads(decision_path.read_text(encoding="utf-8"))
        assert data["action"] == "create"

    def test_decide_with_projects_dir(self, batch_dir, tmp_path):
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            run_decompose(batch_dir, MagicMock())

        # Create a project directory (empty — no projects)
        projects = tmp_path / "projects"
        projects.mkdir()

        with patch("toyshop.llm.chat_with_tool", _mock_decide_chat):
            decision = run_decide(batch_dir, MagicMock(), projects_dir=projects)

        assert decision.action == "create"


# ---------------------------------------------------------------------------
# TestRunEnrich
# ---------------------------------------------------------------------------


class TestRunEnrich:
    def test_enrich_requires_decomposition(self, batch_dir):
        with pytest.raises(FileNotFoundError, match="decomposition.json"):
            run_enrich(batch_dir)

    def test_enrich_creates_markdown(self, batch_dir):
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            run_decompose(batch_dir, MagicMock())

        enriched = run_enrich(batch_dir)

        assert "Enriched Requirement" in enriched
        assert "Combat Logic" in enriched
        assert "Item Registration" in enriched
        out_path = batch_dir.batch_dir / "enriched_requirement.md"
        assert out_path.exists()

    def test_enrich_includes_decision(self, batch_dir):
        with patch("toyshop.llm.chat_with_tool", _mock_decompose_chat):
            run_decompose(batch_dir, MagicMock())

        with patch("toyshop.llm.chat_with_tool", _mock_decide_chat):
            run_decide(batch_dir, MagicMock())

        enriched = run_enrich(batch_dir)
        assert "CREATE" in enriched


# ---------------------------------------------------------------------------
# TestRunBatchWithRefs
# ---------------------------------------------------------------------------


class TestRunBatchWithRefs:
    def test_full_pipeline_create(self, tmp_path):
        """Test the full create pipeline with mocked LLM calls."""
        pm_root = tmp_path / "pm"

        # We need to mock multiple LLM calls in sequence
        call_count = {"n": 0}

        def multi_mock(llm, system, user, name, desc, params):
            call_count["n"] += 1
            # First call: decompose
            if "aspects" in str(params.get("properties", {})):
                return _mock_decompose_chat(llm, system, user, name, desc, params)
            # Decision call
            if "action" in str(params.get("properties", {})) and "create" in str(params.get("properties", {}).get("action", {}).get("enum", [])):
                return _mock_decide_chat(llm, system, user, name, desc, params)
            # Score call
            if "scores" in str(params.get("properties", {})):
                return _mock_score_chat(llm, system, user, name, desc, params)
            # Default: return something reasonable
            return None

        with patch("toyshop.llm.chat_with_tool", multi_mock), \
             patch("toyshop.pm.run_spec_generation") as mock_spec, \
             patch("toyshop.pm.prepare_tasks") as mock_tasks, \
             patch("toyshop.pm.run_batch_tdd") as mock_tdd:

            # Mock spec generation to not fail
            def fake_spec(batch, llm, user_input_override=None, stage_name=None):
                batch.status = "in_progress"
                return batch
            mock_spec.side_effect = fake_spec
            mock_tasks.return_value = []

            from toyshop.tdd_pipeline import TDDResult
            mock_tdd.return_value = TDDResult(success=True, summary="All passed")

            batch = run_batch_with_refs(
                pm_root, "test_mod", "Create a combat mod",
                project_type="java-minecraft",
            )

        # Verify artifacts were created
        assert (batch.batch_dir / "decomposition.json").exists()
        assert (batch.batch_dir / "decision.json").exists()
        assert (batch.batch_dir / "enriched_requirement.md").exists()

        # Verify spec was called with enriched requirement
        mock_spec.assert_called_once()
        call_kwargs = mock_spec.call_args
        assert call_kwargs[1].get("user_input_override") is not None
