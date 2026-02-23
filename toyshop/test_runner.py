"""Test runner abstractions for ToyShop TDD pipeline.

Provides TestRunner ABC and PytestRunner (extracted from tdd_pipeline.py).
"""

from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------


@dataclass
class PerTestResult:
    """Individual test result."""
    test_id: str           # e.g. "tests/test_calc.py::test_add"
    status: str            # "passed" | "failed" | "error" | "skipped"
    failure_message: str = ""


@dataclass
class TestRunResult:
    """Parsed test output."""
    __test__ = False  # prevent pytest collection
    all_passed: bool
    total: int
    passed: int
    failed: int
    errors: int
    output: str
    per_test: list[PerTestResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class TestRunner(ABC):
    """Abstract interface for running tests and parsing output."""

    @abstractmethod
    def run_tests(
        self,
        workspace: Path,
        test_dirs: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        timeout: int = 300,
    ) -> TestRunResult:
        """Run all tests in the workspace. Returns parsed results."""
        ...

    @abstractmethod
    def run_single_test(
        self,
        workspace: Path,
        test_id: str,
        timeout: int = 120,
    ) -> TestRunResult:
        """Run a single test by ID. Returns parsed results."""
        ...

    @abstractmethod
    def parse_output(self, output: str) -> TestRunResult:
        """Parse raw test output into structured results."""
        ...


# ---------------------------------------------------------------------------
# Pytest implementation
# ---------------------------------------------------------------------------


class PytestRunner(TestRunner):
    """Pytest-based test runner — extracted from tdd_pipeline.py."""

    def run_tests(
        self,
        workspace: Path,
        test_dirs: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        timeout: int = 300,
    ) -> TestRunResult:
        """Run pytest in subprocess."""
        if test_dirs is None:
            test_dirs = ["tests/"]

        cmd = ["python3", "-m", "pytest"] + test_dirs + ["-v", "--tb=long"]
        for pat in (ignore_patterns or []):
            cmd.extend(["--ignore", pat])

        try:
            result = subprocess.run(
                cmd, cwd=workspace, capture_output=True, text=True, timeout=timeout,
            )
            combined = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            combined = f"pytest timed out after {timeout}s"
        except Exception as e:
            combined = f"pytest execution error: {e}"

        parsed = self.parse_output(combined)
        parsed.per_test = self._parse_per_test_results(combined)
        return parsed

    def run_single_test(
        self,
        workspace: Path,
        test_id: str,
        timeout: int = 120,
    ) -> TestRunResult:
        """Run a single pytest test by ID."""
        cmd = ["python3", "-m", "pytest", test_id, "-v", "--tb=long"]
        try:
            result = subprocess.run(
                cmd, cwd=workspace, capture_output=True, text=True, timeout=timeout,
            )
            combined = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            combined = f"pytest timed out after {timeout}s"
        except Exception as e:
            combined = f"pytest execution error: {e}"

        parsed = self.parse_output(combined)
        parsed.per_test = self._parse_per_test_results(combined)
        return parsed

    def parse_output(self, output: str) -> TestRunResult:
        """Parse pytest output to extract pass/fail counts."""
        passed = 0
        failed = 0
        errors = 0

        summary_match = re.search(r"(\d+)\s+passed", output)
        if summary_match:
            passed = int(summary_match.group(1))

        fail_match = re.search(r"(\d+)\s+failed", output)
        if fail_match:
            failed = int(fail_match.group(1))

        error_match = re.search(r"(\d+)\s+error", output)
        if error_match:
            errors = int(error_match.group(1))

        total = passed + failed + errors
        all_passed = total > 0 and failed == 0 and errors == 0

        return TestRunResult(
            all_passed=all_passed,
            total=total,
            passed=passed,
            failed=failed,
            errors=errors,
            output=output,
        )

    def _parse_per_test_results(self, output: str) -> list[PerTestResult]:
        """Parse pytest -v output into per-test results."""
        results: list[PerTestResult] = []
        seen: set[str] = set()

        for line in output.split("\n"):
            line = line.strip()
            m = re.match(
                r"([\w/\\._-]+::[\w_]+(?:::[\w_]+)?)\s+(PASSED|FAILED|ERROR|SKIPPED)", line
            )
            if m:
                test_id = m.group(1)
                status = m.group(2).lower()
                if test_id not in seen:
                    seen.add(test_id)
                    results.append(PerTestResult(test_id=test_id, status=status))

        # Extract failure messages from FAILURES section
        failure_blocks: dict[str, str] = {}
        in_failures = False
        current_test = ""
        current_lines: list[str] = []
        for line in output.split("\n"):
            if line.strip().startswith("= FAILURES =") or line.strip().startswith("=== FAILURES ==="):
                in_failures = True
                continue
            if in_failures and (
                line.strip().startswith("= short test summary") or line.strip().startswith("===")
            ):
                if current_test and current_lines:
                    failure_blocks[current_test] = "\n".join(current_lines)
                break
            if in_failures:
                fm = re.match(r"_{3,}\s*([\w/\\._:-]+)\s*_{3,}", line)
                if fm:
                    if current_test and current_lines:
                        failure_blocks[current_test] = "\n".join(current_lines)
                    current_test = fm.group(1).strip()
                    current_lines = []
                elif current_test:
                    current_lines.append(line)

        for r in results:
            if r.status in ("failed", "error"):
                test_name = r.test_id.split("::")[-1] if "::" in r.test_id else r.test_id
                msg = failure_blocks.get(r.test_id, "") or failure_blocks.get(test_name, "")
                if msg:
                    r.failure_message = msg[:2000]

        return results


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_RUNNER_REGISTRY: dict[str, type[TestRunner]] = {
    "pytest": PytestRunner,
}


def register_test_runner(framework: str, runner_cls: type[TestRunner]) -> None:
    """Register a TestRunner class for a test framework ID."""
    _RUNNER_REGISTRY[framework] = runner_cls


def get_test_runner(framework: str) -> TestRunner:
    """Get a TestRunner instance by framework ID. Raises KeyError if not found."""
    if framework not in _RUNNER_REGISTRY:
        available = ", ".join(sorted(_RUNNER_REGISTRY.keys()))
        raise KeyError(f"No test runner for '{framework}'. Available: {available}")
    return _RUNNER_REGISTRY[framework]()
