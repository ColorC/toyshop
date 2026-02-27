"""Tests for smart_bootstrap — LLM-driven intelligent bootstrap."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from toyshop.smart_bootstrap import (
    ExplorationState,
    SmartBootstrapResult,
    _exec_list_directory,
    _exec_read_file,
    _exec_search_code,
    _exec_ast_scan_module,
    _is_test_file,
    _dispatch_tool,
    _build_exploration_user_content,
    _load_toyignore,
    _should_skip_dir,
    run_exploration,
    smart_bootstrap,
)
from toyshop.storage.database import init_database, close_database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path) -> Path:
    """Create a minimal Python project for testing."""
    src = tmp_path / "myproject"
    src.mkdir()
    (src / "main.py").write_text(
        "from myproject.utils import helper\n\n"
        "class App:\n"
        "    def run(self) -> str:\n"
        "        return helper('hello')\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        "def helper(s: str) -> str:\n"
        "    return s.upper()\n",
        encoding="utf-8",
    )
    (src / "tests").mkdir()
    (src / "tests" / "test_main.py").write_text(
        "def test_app():\n    pass\n",
        encoding="utf-8",
    )
    (src / "pyproject.toml").write_text(
        '[project]\nname = "myproject"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return src


@pytest.fixture(autouse=True)
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    yield db_path
    close_database()


# ---------------------------------------------------------------------------
# TestExplorationTools
# ---------------------------------------------------------------------------


class TestExplorationTools:
    def test_list_directory_returns_entries(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_list_directory(proj, ".")
        assert "main.py" in result
        assert "utils.py" in result
        assert "tests/" in result

    def test_list_directory_rejects_path_escape(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_list_directory(proj, "../../../etc")
        assert "error" in result.lower()

    def test_list_directory_nonexistent(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_list_directory(proj, "nonexistent")
        assert "error" in result.lower()

    def test_read_file_returns_content(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_read_file(proj, "main.py")
        assert "class App" in result
        assert "File: main.py" in result

    def test_read_file_truncates(self, tmp_path):
        proj = _make_project(tmp_path)
        # Write a long file
        (proj / "long.py").write_text("\n".join(f"line_{i}" for i in range(500)))
        result = _exec_read_file(proj, "long.py", max_lines=10)
        assert "truncated" in result.lower()

    def test_read_file_nonexistent(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_read_file(proj, "nope.py")
        assert "error" in result.lower() or "not found" in result.lower()

    def test_search_code_finds_pattern(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_search_code(proj, "class App", "*.py")
        assert "App" in result

    def test_search_code_no_matches(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_search_code(proj, "NONEXISTENT_PATTERN_XYZ", "*.py")
        assert "No matches" in result

    def test_ast_scan_module_extracts_structure(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_ast_scan_module(proj, "main.py")
        assert "App" in result
        assert "run" in result

    def test_ast_scan_module_nonexistent(self, tmp_path):
        proj = _make_project(tmp_path)
        result = _exec_ast_scan_module(proj, "nope.py")
        assert "error" in result.lower()


# ---------------------------------------------------------------------------
# TestIsTestFile
# ---------------------------------------------------------------------------


class TestIsTestFile:
    def test_test_prefix(self):
        assert _is_test_file("test_main.py")

    def test_test_suffix(self):
        assert _is_test_file("main_test.py")

    def test_tests_directory(self):
        assert _is_test_file("tests/test_main.py")

    def test_conftest(self):
        assert _is_test_file("conftest.py")

    def test_source_file(self):
        assert not _is_test_file("main.py")

    def test_source_in_src(self):
        assert not _is_test_file("src/core/main.py")


# ---------------------------------------------------------------------------
# TestToyignore
# ---------------------------------------------------------------------------


class TestToyignore:
    def test_load_toyignore_reads_patterns(self, tmp_path):
        (tmp_path / ".toyignore").write_text("mods/\nharness/\n# comment\n\ntests/\n")
        patterns = _load_toyignore(tmp_path)
        assert patterns == ["mods/", "harness/", "tests/"]

    def test_load_toyignore_missing_file(self, tmp_path):
        patterns = _load_toyignore(tmp_path)
        assert patterns == []

    def test_should_skip_dir_default(self):
        assert _should_skip_dir("__pycache__", [])
        assert _should_skip_dir(".git", [])
        assert not _should_skip_dir("src", [])

    def test_should_skip_dir_with_patterns(self):
        assert _should_skip_dir("mods", ["mods/", "harness/"])
        assert _should_skip_dir("harness", ["mods/", "harness/"])
        assert not _should_skip_dir("sdk", ["mods/", "harness/"])

    def test_list_directory_respects_toyignore(self, tmp_path):
        proj = _make_project(tmp_path)
        (proj / "ignored_dir").mkdir()
        (proj / "ignored_dir" / "file.py").write_text("x = 1")
        result = _exec_list_directory(proj, ".", ["ignored_dir/"])
        assert "ignored_dir" not in result
        assert "main.py" in result

    def test_snapshot_respects_ignore_patterns(self, tmp_path):
        from toyshop.snapshot import create_code_version
        proj = _make_project(tmp_path)
        # Add a file in an ignored directory
        ignored = proj / "vendor"
        ignored.mkdir()
        (ignored / "lib.py").write_text("def vendored(): pass")
        # Without ignore
        snap1 = create_code_version(proj, "test")
        names1 = {m.name for m in snap1.modules}
        assert "lib" in names1
        # With ignore
        snap2 = create_code_version(proj, "test", ignore_patterns=["vendor/"])
        names2 = {m.name for m in snap2.modules}
        assert "lib" not in names2


# ---------------------------------------------------------------------------
# TestExplorationLoop
# ---------------------------------------------------------------------------


class TestExplorationLoop:
    def test_completes_on_complete_exploration(self, tmp_path):
        """Exploration terminates when LLM calls complete_exploration."""
        proj = _make_project(tmp_path)
        state = ExplorationState(
            project_root=str(proj),
            project_name="test",
            max_iterations=5,
        )

        call_count = 0

        def mock_chat_with_tool(llm, system, user, name, desc, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"action": "list_directory", "reasoning": "Start", "directory_path": "."}
            if call_count == 2:
                return {"action": "read_file", "reasoning": "Read main", "file_path": "main.py"}
            return {
                "action": "complete_exploration",
                "reasoning": "Done",
                "architecture_summary": "Simple project",
                "technology_stack": ["python"],
                "key_patterns": ["oop"],
                "module_descriptions": [
                    {"name": "main", "file_path": "main.py", "responsibilities": ["Entry point"]},
                ],
            }

        with patch("toyshop.llm.chat_with_tool", side_effect=mock_chat_with_tool):
            result = run_exploration(MagicMock(), state)

        assert result.completed
        assert result.iteration == 3
        assert result.architecture_summary == "Simple project"
        assert len(result.module_descriptions) == 1

    def test_force_completes_at_max_iterations(self, tmp_path):
        """Exploration force-completes when max iterations reached."""
        proj = _make_project(tmp_path)
        state = ExplorationState(
            project_root=str(proj),
            project_name="test",
            max_iterations=3,
        )

        def mock_chat(llm, system, user, name, desc, params):
            return {"action": "list_directory", "reasoning": "Exploring", "directory_path": "."}

        with patch("toyshop.llm.chat_with_tool", side_effect=mock_chat):
            result = run_exploration(MagicMock(), state)

        assert result.completed
        assert result.iteration == 3
        assert len(result.findings) == 3

    def test_accumulates_findings(self, tmp_path):
        """Findings accumulate across iterations."""
        proj = _make_project(tmp_path)
        state = ExplorationState(
            project_root=str(proj),
            project_name="test",
            max_iterations=2,
        )

        calls = [
            {"action": "list_directory", "reasoning": "Root", "directory_path": "."},
            {"action": "read_file", "reasoning": "Main", "file_path": "main.py"},
        ]
        call_idx = [0]

        def mock_chat(llm, system, user, name, desc, params):
            r = calls[call_idx[0]]
            call_idx[0] += 1
            return r

        with patch("toyshop.llm.chat_with_tool", side_effect=mock_chat):
            result = run_exploration(MagicMock(), state)

        assert len(result.findings) == 2
        assert "." in result.dirs_listed
        assert "main.py" in result.files_read

    def test_handles_none_tool_result(self, tmp_path):
        """Handles LLM returning None gracefully."""
        proj = _make_project(tmp_path)
        state = ExplorationState(
            project_root=str(proj),
            project_name="test",
            max_iterations=2,
        )

        def mock_chat(llm, system, user, name, desc, params):
            return None

        with patch("toyshop.llm.chat_with_tool", side_effect=mock_chat):
            result = run_exploration(MagicMock(), state)

        assert result.completed
        assert len(result.findings) == 0



# ---------------------------------------------------------------------------
# TestBuildExplorationContent
# ---------------------------------------------------------------------------


class TestBuildExplorationContent:
    def test_includes_project_info(self):
        state = ExplorationState(
            project_root="/tmp/test",
            project_name="myproj",
            max_iterations=10,
        )
        content = _build_exploration_user_content(state)
        assert "myproj" in content
        assert "1 of 10" in content

    def test_includes_files_read(self):
        state = ExplorationState(
            project_root="/tmp/test",
            project_name="myproj",
            files_read={"main.py", "utils.py"},
        )
        content = _build_exploration_user_content(state)
        assert "main.py" in content

    def test_warns_low_iterations(self):
        state = ExplorationState(
            project_root="/tmp/test",
            project_name="myproj",
            iteration=8,
            max_iterations=10,
        )
        content = _build_exploration_user_content(state)
        assert "low on iterations" in content.lower()


# ---------------------------------------------------------------------------
# TestSmartBootstrapIntegration
# ---------------------------------------------------------------------------


class TestSmartBootstrapIntegration:
    def _mock_chat_factory(self):
        """Build a mock chat_with_tool that handles exploration + synthesis."""
        exploration_calls = [0]

        def mock_chat(llm, system, user, name, desc, params):
            if name == "exploration_action":
                exploration_calls[0] += 1
                if exploration_calls[0] <= 2:
                    return {"action": "list_directory", "reasoning": "Explore", "directory_path": "."}
                return {
                    "action": "complete_exploration",
                    "reasoning": "Done",
                    "architecture_summary": "Simple project with App and utils",
                    "technology_stack": ["python3"],
                    "key_patterns": ["oop"],
                    "module_descriptions": [
                        {"name": "main", "file_path": "main.py",
                         "responsibilities": ["Entry point"], "dependencies": ["utils"]},
                        {"name": "utils", "file_path": "utils.py",
                         "responsibilities": ["Helpers"], "dependencies": []},
                    ],
                }
            # Synthesis calls — return valid data for each tool
            if name == "generate_proposal":
                return {
                    "projectName": "myproject",
                    "background": "Existing Python project with App and utils",
                    "problem": "No formal architecture documentation",
                    "goals": ["Document architecture", "Enable change tracking"],
                }
            if name == "generate_design":
                return {
                    "requirement": "Document myproject architecture",
                    "goals": [
                        {"id": "G1", "description": "Document architecture"},
                    ],
                    "decisions": [
                        {
                            "id": "1", "title": "Simple OOP design",
                            "context": "Small project",
                            "decision": "Use classes for core logic",
                            "consequences": "Easy to understand",
                        },
                    ],
                    "module_descriptions": {
                        "main": "Application entry point with App class",
                        "utils": "String helper functions",
                    },
                    "risks": [],
                    "tradeoffs": [],
                }
            if name == "generate_tasks":
                return {
                    "tasks": [
                        {
                            "id": "1", "title": "Implement main module",
                            "description": "Core application logic",
                            "status": "completed", "dependencies": [],
                        },
                        {
                            "id": "2", "title": "Implement utils module",
                            "description": "Helper functions",
                            "status": "completed", "dependencies": [],
                        },
                    ],
                }
            if name == "generate_spec":
                return {
                    "scenarios": [
                        {
                            "id": "S1", "name": "App runs successfully",
                            "given": "the myproject codebase",
                            "when": "App.run() is called",
                            "then": "it returns the expected result",
                        },
                    ],
                }
            return None

        return mock_chat

    def test_bootstrap_with_mock_llm(self, tmp_path):
        """Full bootstrap with mocked LLM exploration + synthesis."""
        close_database()

        proj = _make_project(tmp_path)
        db_path = tmp_path / "test_llm.db"

        mock_llm = MagicMock()

        with patch("toyshop.llm.chat_with_tool", side_effect=self._mock_chat_factory()):
            result = smart_bootstrap(
                project_name="myproject",
                workspace=proj,
                llm=mock_llm,
                db_path=db_path,
            )

        assert result.project_id
        assert result.version_number == 1
        assert result.exploration_iterations == 3
        assert result.modules_count >= 2

        # Verify wiki version has frozen openspec
        from toyshop.storage.wiki import get_latest_version
        version = get_latest_version(result.project_id)
        assert version is not None
        assert version.design_md is not None
        assert version.proposal_md is not None
        assert version.tasks_md is not None
        assert version.spec_md is not None

        # Verify design.md is parseable
        from toyshop.tdd_pipeline import _parse_design_modules
        modules = _parse_design_modules(version.design_md)
        assert len(modules) >= 2

        close_database()

    def test_bootstrap_idempotent(self, tmp_path):
        """Smart bootstrap is idempotent."""
        close_database()

        proj = _make_project(tmp_path)
        db_path = tmp_path / "test_idem.db"

        mock_llm = MagicMock()

        with patch("toyshop.llm.chat_with_tool", side_effect=self._mock_chat_factory()):
            r1 = smart_bootstrap("myproject", proj, llm=mock_llm, db_path=db_path)
        close_database()

        with patch("toyshop.llm.chat_with_tool", side_effect=self._mock_chat_factory()):
            r2 = smart_bootstrap("myproject", proj, llm=mock_llm, db_path=db_path)
        assert r1.project_id == r2.project_id

        close_database()

    def test_synthesis_failure_raises(self, tmp_path):
        """Synthesis raises RuntimeError when LLM returns None."""
        close_database()

        proj = _make_project(tmp_path)
        db_path = tmp_path / "test_fail.db"

        exploration_calls = [0]

        def mock_chat(llm, system, user, name, desc, params):
            if name == "exploration_action":
                exploration_calls[0] += 1
                if exploration_calls[0] <= 1:
                    return {"action": "list_directory", "reasoning": "Explore", "directory_path": "."}
                return {
                    "action": "complete_exploration",
                    "reasoning": "Done",
                    "architecture_summary": "Simple project",
                    "technology_stack": ["python3"],
                    "key_patterns": [],
                    "module_descriptions": [],
                }
            # All synthesis calls return None → should raise
            return None

        mock_llm = MagicMock()

        with patch("toyshop.llm.chat_with_tool", side_effect=mock_chat):
            with pytest.raises(RuntimeError, match="LLM returned no tool call"):
                smart_bootstrap(
                    project_name="myproject",
                    workspace=proj,
                    llm=mock_llm,
                    db_path=db_path,
                )

        close_database()
