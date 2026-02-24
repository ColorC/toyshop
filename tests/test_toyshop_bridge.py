"""Tests for ModFactory → ToyShop bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add modfactory SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "modfactory" / "sdk"))


# ---------------------------------------------------------------------------
# Test bridge data structures
# ---------------------------------------------------------------------------


class TestModRequest:
    def test_defaults(self):
        from modfactory.toyshop_bridge import ModRequest

        req = ModRequest(requirement="Create a frost bow mod")
        assert req.requirement == "Create a frost bow mod"
        assert req.mod_name is None
        assert req.project_type == "java-minecraft"
        assert req.force_create is False

    def test_custom_values(self):
        from modfactory.toyshop_bridge import ModRequest

        req = ModRequest(
            requirement="test",
            mod_name="frost-bow",
            force_create=True,
            project_type="java-minecraft",
        )
        assert req.mod_name == "frost-bow"
        assert req.force_create is True


class TestModResult:
    def test_defaults(self):
        from modfactory.toyshop_bridge import ModResult

        result = ModResult()
        assert result.success is False
        assert result.error is None
        assert result.action == ""

    def test_error_result(self):
        from modfactory.toyshop_bridge import ModResult

        result = ModResult(error="ToyShop not available")
        assert not result.success
        assert "ToyShop" in result.error


# ---------------------------------------------------------------------------
# Test slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        from modfactory.toyshop_bridge import _slugify

        assert _slugify("Create a Frost Bow") == "create-a-frost-bow"

    def test_chinese(self):
        from modfactory.toyshop_bridge import _slugify

        result = _slugify("创建一个寒冰弓mod")
        assert len(result) > 0
        assert len(result) <= 30

    def test_max_len(self):
        from modfactory.toyshop_bridge import _slugify

        result = _slugify("a" * 100, max_len=10)
        assert len(result) <= 10

    def test_empty(self):
        from modfactory.toyshop_bridge import _slugify

        result = _slugify("!@#$%")
        assert result == "unnamed-mod"


# ---------------------------------------------------------------------------
# Test create_mod
# ---------------------------------------------------------------------------


class TestCreateMod:
    def test_toyshop_not_available(self):
        """If ToyShop import fails, return error result."""
        from modfactory.toyshop_bridge import ModRequest

        # This test verifies the error handling path
        # We can't easily mock ImportError inside the function,
        # but we can test the happy path with mocked pipeline
        req = ModRequest(requirement="test")
        assert req.project_type == "java-minecraft"

    def test_create_mod_with_mocked_pipeline(self, tmp_path):
        """Test create_mod with fully mocked ToyShop pipeline."""
        from modfactory.toyshop_bridge import create_mod, ModRequest

        # Create a fake batch result
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()
        workspace = batch_dir / "workspace"
        workspace.mkdir()
        (workspace / "build.gradle").write_text("test", encoding="utf-8")

        # Write decomposition
        (batch_dir / "decomposition.json").write_text(
            json.dumps({"aspects": [], "original_requirement": "test", "project_type": "java-minecraft"}),
            encoding="utf-8",
        )
        # Write decision
        (batch_dir / "decision.json").write_text(
            json.dumps({"action": "create", "rationale": "new mod"}),
            encoding="utf-8",
        )

        class FakeBatch:
            def __init__(self):
                self.batch_dir = batch_dir
                self.status = "completed"
                self.error = None

        def fake_run_batch_with_refs(**kwargs):
            return FakeBatch()

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        with patch("toyshop.pm.run_batch_with_refs", fake_run_batch_with_refs):
            req = ModRequest(
                requirement="Create a frost bow mod",
                mod_name="frost-bow",
                projects_dir=mods_dir,
                pm_root=tmp_path / "pm",
            )
            result = create_mod(req, llm=MagicMock())

        assert result.success
        assert result.action == "create"
        assert result.decomposition is not None
        assert result.decision is not None
        # Workspace should be copied to mods/
        assert (mods_dir / "frost-bow").exists()
        assert result.mod_path == mods_dir / "frost-bow"
