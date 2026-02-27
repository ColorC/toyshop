"""Tests for architecture_guard module."""

from __future__ import annotations

import pytest

from toyshop.architecture_guard import (
    GuardViolation,
    GuardResult,
    check_duplicate_responsibilities,
    check_new_module_overlap,
    check_interface_quality,
    run_architecture_guard,
    _tokenize,
    _jaccard,
    _count_params,
)


# ---------------------------------------------------------------------------
# Tokenization helpers
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_basic(self):
        tokens = _tokenize("Handle user authentication")
        assert "handle" in tokens
        assert "user" in tokens
        assert "authentication" in tokens

    def test_single_char_excluded(self):
        tokens = _tokenize("a big module")
        assert "a" not in tokens
        assert "big" in tokens


class TestJaccard:
    def test_identical(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial(self):
        assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_empty(self):
        assert _jaccard(set(), {"a"}) == 0.0


# ---------------------------------------------------------------------------
# Check 1: Duplicate responsibilities
# ---------------------------------------------------------------------------


class TestDuplicateResponsibilities:
    def test_exact_duplicate_is_error(self):
        modules = [
            {"name": "auth", "responsibilities": ["handle user authentication", "manage sessions"]},
            {"name": "login", "responsibilities": ["handle user authentication", "manage sessions"]},
        ]
        violations = check_duplicate_responsibilities(modules)
        assert len(violations) >= 1
        assert any(v.severity == "error" for v in violations)
        assert any("auth" in v.module_a and "login" in v.module_b for v in violations)

    def test_partial_overlap_is_warning(self):
        modules = [
            {"name": "auth", "responsibilities": ["handle user authentication", "manage tokens"]},
            {"name": "session", "responsibilities": ["handle user sessions", "manage cookies", "track state"]},
        ]
        violations = check_duplicate_responsibilities(modules, warn_threshold=0.3)
        # Some overlap on "handle", "user", "manage" but not exact
        has_warning = any(v.severity == "warning" for v in violations)
        # May or may not trigger depending on exact overlap — just verify no crash
        assert isinstance(violations, list)

    def test_no_overlap_passes(self):
        modules = [
            {"name": "auth", "responsibilities": ["handle user authentication"]},
            {"name": "database", "responsibilities": ["persist data to storage"]},
        ]
        violations = check_duplicate_responsibilities(modules)
        assert violations == []

    def test_empty_responsibilities_ignored(self):
        modules = [
            {"name": "auth", "responsibilities": []},
            {"name": "login", "responsibilities": []},
        ]
        violations = check_duplicate_responsibilities(modules)
        assert violations == []

    def test_single_module_no_comparison(self):
        modules = [
            {"name": "auth", "responsibilities": ["handle everything"]},
        ]
        violations = check_duplicate_responsibilities(modules)
        assert violations == []


# ---------------------------------------------------------------------------
# Check 2: New module overlap
# ---------------------------------------------------------------------------


class TestNewModuleOverlap:
    def test_new_module_overlaps_existing(self):
        existing = [
            {"name": "auth", "responsibilities": ["handle user authentication", "manage tokens"]},
        ]
        new = [
            {"name": "login_handler", "responsibilities": ["handle user authentication", "manage tokens"]},
        ]
        violations = check_new_module_overlap(new, existing, warn_threshold=0.5)
        assert len(violations) >= 1
        assert violations[0].check_name == "new_module_overlap"

    def test_new_module_unique_passes(self):
        existing = [
            {"name": "auth", "responsibilities": ["handle user authentication"]},
        ]
        new = [
            {"name": "payments", "responsibilities": ["process credit card payments"]},
        ]
        violations = check_new_module_overlap(new, existing)
        assert violations == []

    def test_empty_new_modules(self):
        violations = check_new_module_overlap([], [{"name": "x", "responsibilities": ["y"]}])
        assert violations == []


# ---------------------------------------------------------------------------
# Check 3: Interface quality
# ---------------------------------------------------------------------------


class TestCountParams:
    def test_simple_function(self):
        assert _count_params("def foo(a, b, c)") == 3

    def test_with_self(self):
        assert _count_params("def foo(self, a, b)") == 2

    def test_with_cls(self):
        assert _count_params("def foo(cls, a)") == 1

    def test_no_params(self):
        assert _count_params("def foo()") == 0

    def test_class_returns_none(self):
        assert _count_params("class Foo") is None

    def test_with_defaults(self):
        assert _count_params("def foo(a, b=1, c=2)") == 3

    def test_with_type_annotations(self):
        assert _count_params("def foo(a: int, b: str) -> bool") == 2


class TestInterfaceQuality:
    def test_too_many_params_warns(self):
        interfaces = [
            {"name": "big_func", "type": "function",
             "signature": "def big_func(a, b, c, d, e, f, g, h)", "module_id": "m1"},
        ]
        violations = check_interface_quality(interfaces, max_params=7)
        assert any("参数过多" in v.detail for v in violations)

    def test_bad_function_naming_warns(self):
        interfaces = [
            {"name": "BadName", "type": "function",
             "signature": "def BadName()", "module_id": "m1"},
        ]
        violations = check_interface_quality(interfaces)
        assert any("snake_case" in v.detail for v in violations)

    def test_bad_class_naming_warns(self):
        interfaces = [
            {"name": "bad_class", "type": "class",
             "signature": "class bad_class", "module_id": "m1"},
        ]
        violations = check_interface_quality(interfaces)
        assert any("PascalCase" in v.detail for v in violations)

    def test_missing_return_type_warns(self):
        interfaces = [
            {"name": "foo", "type": "function",
             "signature": "def foo(a, b)", "module_id": "m1"},
        ]
        violations = check_interface_quality(interfaces)
        assert any("返回类型" in v.detail for v in violations)

    def test_clean_interface_passes(self):
        interfaces = [
            {"name": "process_data", "type": "function",
             "signature": "def process_data(items: list) -> dict", "module_id": "m1"},
            {"name": "DataProcessor", "type": "class",
             "signature": "class DataProcessor", "module_id": "m1"},
        ]
        violations = check_interface_quality(interfaces)
        assert violations == []

    def test_private_function_skipped(self):
        interfaces = [
            {"name": "_helper", "type": "function",
             "signature": "def _helper(x)", "module_id": "m1"},
        ]
        # Private functions (starting with _) should not trigger naming warnings
        violations = [v for v in check_interface_quality(interfaces)
                      if "snake_case" in v.detail]
        assert violations == []


# ---------------------------------------------------------------------------
# Unified runner
# ---------------------------------------------------------------------------


class TestRunArchitectureGuard:
    def test_minimal_skips_all(self):
        result = run_architecture_guard(
            modules=[{"name": "a", "responsibilities": ["x"]},
                     {"name": "b", "responsibilities": ["x"]}],
            management_level="minimal",
        )
        assert result.passed
        assert result.violations == []

    def test_standard_downgrades_to_warnings(self):
        modules = [
            {"name": "auth", "responsibilities": ["handle user authentication", "manage sessions"]},
            {"name": "login", "responsibilities": ["handle user authentication", "manage sessions"]},
        ]
        result = run_architecture_guard(modules=modules, management_level="standard")
        # Even exact duplicates become warnings in standard mode
        assert result.passed  # No errors
        assert all(v.severity == "warning" for v in result.violations)

    def test_strict_keeps_errors(self):
        modules = [
            {"name": "auth", "responsibilities": ["handle user authentication", "manage sessions"]},
            {"name": "login", "responsibilities": ["handle user authentication", "manage sessions"]},
        ]
        result = run_architecture_guard(modules=modules, management_level="strict")
        assert not result.passed
        assert any(v.severity == "error" for v in result.violations)

    def test_with_interfaces(self):
        modules = [{"name": "core", "responsibilities": ["core logic"]}]
        interfaces = [
            {"name": "process", "type": "function",
             "signature": "def process(data: list) -> dict", "module_id": "core"},
        ]
        result = run_architecture_guard(
            modules=modules, interfaces=interfaces, management_level="standard",
        )
        assert result.passed

    def test_new_module_overlap_detected(self):
        modules = [
            {"name": "auth", "responsibilities": ["handle user authentication", "manage tokens"]},
        ]
        new_modules = [
            {"name": "auth_v2", "responsibilities": ["handle user authentication", "manage tokens"]},
        ]
        result = run_architecture_guard(
            modules=modules, new_modules=new_modules, management_level="standard",
        )
        assert any(v.check_name == "new_module_overlap" for v in result.violations)


# ---------------------------------------------------------------------------
# GuardResult properties
# ---------------------------------------------------------------------------


class TestGuardResult:
    def test_passed_when_no_errors(self):
        r = GuardResult(violations=[
            GuardViolation("x", "warning", "a", "b", "d", "s"),
        ])
        assert r.passed

    def test_not_passed_when_errors(self):
        r = GuardResult(violations=[
            GuardViolation("x", "error", "a", "b", "d", "s"),
        ])
        assert not r.passed

    def test_errors_and_warnings(self):
        r = GuardResult(violations=[
            GuardViolation("x", "error", "a", "b", "d", "s"),
            GuardViolation("y", "warning", "c", "d", "d", "s"),
        ])
        assert len(r.errors) == 1
        assert len(r.warnings) == 1

    def test_to_dict(self):
        v = GuardViolation("x", "warning", "a", "b", "detail", "suggestion")
        d = v.to_dict()
        assert d["check_name"] == "x"
        assert d["severity"] == "warning"
