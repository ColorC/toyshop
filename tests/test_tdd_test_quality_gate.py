"""Tests for TDD Phase-2 test-quality gate helpers."""

from __future__ import annotations

from pathlib import Path

from toyshop.tdd_pipeline import (
    _find_test_placeholder_issues,
    _run_pytest_collect_only,
    _test_generation_quality_issues,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_find_test_placeholder_issues_detects_common_placeholders(tmp_path: Path):
    test_file = tmp_path / "tests" / "test_placeholders.py"
    _write(
        test_file,
        """import pytest

def test_has_pass():
    # TODO: fill later
    pass

def test_has_notimplemented():
    raise NotImplementedError("TODO")

def test_has_skip():
    pytest.skip("not ready")
""",
    )

    issues = _find_test_placeholder_issues(test_file, "tests/test_placeholders.py")

    assert any("contains TODO marker" in msg for msg in issues)
    assert any("uses placeholder `pass`" in msg for msg in issues)
    assert any("NotImplementedError placeholder" in msg for msg in issues)
    assert any("uses pytest.skip/xfail placeholder" in msg for msg in issues)


def test_run_pytest_collect_only_success(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_ok.py",
        """def test_ok():
    assert True
""",
    )
    ok, output = _run_pytest_collect_only(tmp_path)
    assert ok is True, output


def test_test_generation_quality_issues_reports_collect_failure(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_bad_import.py",
        """from missing_module import missing_symbol

def test_anything():
    assert missing_symbol is not None
""",
    )
    issues = _test_generation_quality_issues(
        tmp_path,
        ["tests/test_bad_import.py"],
    )
    assert any("pytest --collect-only failed" in msg for msg in issues)
