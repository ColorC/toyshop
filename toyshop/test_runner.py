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
# Gradle / JUnit implementation
# ---------------------------------------------------------------------------


class GradleTestRunner(TestRunner):
    """Gradle + JUnit 5 test runner — parses JUnit XML reports."""

    def run_tests(
        self,
        workspace: Path,
        test_dirs: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        timeout: int = 600,
    ) -> TestRunResult:
        """Run ./gradlew test and parse JUnit XML reports."""
        cmd = ["./gradlew", "test", "--no-daemon"]
        try:
            result = subprocess.run(
                cmd, cwd=workspace, capture_output=True, text=True, timeout=timeout,
            )
            combined = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            combined = f"gradlew test timed out after {timeout}s"
        except Exception as e:
            combined = f"gradlew execution error: {e}"

        # Try JUnit XML first, fall back to console output parsing
        xml_result = self._parse_junit_xml(workspace)
        if xml_result and xml_result.total > 0:
            xml_result.output = combined
            return xml_result

        parsed = self.parse_output(combined)
        return parsed

    def run_single_test(
        self,
        workspace: Path,
        test_id: str,
        timeout: int = 300,
    ) -> TestRunResult:
        """Run a single test by fully-qualified name (e.g. com.example.CalcTest#testAdd)."""
        cmd = ["./gradlew", "test", "--no-daemon", "--tests", test_id]
        try:
            result = subprocess.run(
                cmd, cwd=workspace, capture_output=True, text=True, timeout=timeout,
            )
            combined = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            combined = f"gradlew test timed out after {timeout}s"
        except Exception as e:
            combined = f"gradlew execution error: {e}"

        xml_result = self._parse_junit_xml(workspace)
        if xml_result and xml_result.total > 0:
            xml_result.output = combined
            return xml_result

        parsed = self.parse_output(combined)
        return parsed

    def parse_output(self, output: str) -> TestRunResult:
        """Parse Gradle test console output as fallback."""
        passed = 0
        failed = 0
        errors = 0

        # Gradle summary: "3 tests completed, 1 failed"
        m = re.search(r"(\d+)\s+tests?\s+completed", output)
        if m:
            total_completed = int(m.group(1))
        else:
            total_completed = 0

        fm = re.search(r"(\d+)\s+failed", output)
        if fm:
            failed = int(fm.group(1))

        em = re.search(r"(\d+)\s+errors?", output)
        if em:
            errors = int(em.group(1))

        passed = max(0, total_completed - failed - errors)
        total = passed + failed + errors
        all_passed = total > 0 and failed == 0 and errors == 0

        # Also check for BUILD SUCCESSFUL / BUILD FAILED
        if total == 0:
            if "BUILD SUCCESSFUL" in output:
                all_passed = True
                passed = 1
                total = 1
            elif "BUILD FAILED" in output:
                all_passed = False
                failed = 1
                total = 1

        return TestRunResult(
            all_passed=all_passed,
            total=total,
            passed=passed,
            failed=failed,
            errors=errors,
            output=output,
        )

    def _parse_junit_xml(self, workspace: Path) -> TestRunResult | None:
        """Parse JUnit XML reports from build/test-results/test/."""
        import xml.etree.ElementTree as ET

        report_dir = workspace / "build" / "test-results" / "test"
        if not report_dir.exists():
            return None

        total_passed = 0
        total_failed = 0
        total_errors = 0
        per_test: list[PerTestResult] = []

        for xml_file in sorted(report_dir.glob("TEST-*.xml")):
            try:
                tree = ET.parse(xml_file)
                root = tree.getroot()
            except (ET.ParseError, OSError):
                continue

            for testsuite in ([root] if root.tag == "testsuite" else root.findall("testsuite")):
                for testcase in testsuite.findall("testcase"):
                    name = testcase.get("name", "")
                    classname = testcase.get("classname", "")
                    test_id = f"{classname}#{name}" if classname else name

                    failure = testcase.find("failure")
                    error = testcase.find("error")
                    skipped = testcase.find("skipped")

                    if failure is not None:
                        total_failed += 1
                        msg = (failure.get("message", "") or failure.text or "")[:2000]
                        per_test.append(PerTestResult(test_id=test_id, status="failed", failure_message=msg))
                    elif error is not None:
                        total_errors += 1
                        msg = (error.get("message", "") or error.text or "")[:2000]
                        per_test.append(PerTestResult(test_id=test_id, status="error", failure_message=msg))
                    elif skipped is not None:
                        per_test.append(PerTestResult(test_id=test_id, status="skipped"))
                    else:
                        total_passed += 1
                        per_test.append(PerTestResult(test_id=test_id, status="passed"))

        total = total_passed + total_failed + total_errors
        if total == 0:
            return None

        return TestRunResult(
            all_passed=total > 0 and total_failed == 0 and total_errors == 0,
            total=total,
            passed=total_passed,
            failed=total_failed,
            errors=total_errors,
            output="",
            per_test=per_test,
        )


# ---------------------------------------------------------------------------
# RCON test runner (Minecraft mod verification — Layer 1A)
# ---------------------------------------------------------------------------


class RconTestRunner(TestRunner):
    """RCON-based Minecraft mod verification.

    Wraps modfactory.verify_rcon.RCONVerifier to test block/item registration
    against a running Minecraft server.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 25575,
        password: str = "modtest",
    ):
        self.host = host
        self.port = port
        self.password = password

    def run_tests(
        self,
        workspace: Path,
        test_dirs: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        timeout: int = 120,
    ) -> TestRunResult:
        """Run RCON verification for a mod.

        Expects workspace to contain a mod with metadata we can extract,
        or test_dirs[0] to be a JSON file with {mod_id, blocks, items}.
        Falls back to Gradle build if RCON is unavailable.
        """
        try:
            from modfactory.verify_rcon import RCONVerifier
        except ImportError:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output="modfactory SDK not installed — cannot run RCON tests",
            )

        # Load test spec from JSON if provided
        spec = self._load_test_spec(workspace, test_dirs)
        if not spec:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output="No RCON test spec found (need rcon_tests.json with mod_id/blocks/items)",
            )

        verifier = RCONVerifier(self.host, self.port, self.password)
        try:
            report = verifier.verify_mod(
                mod_id=spec.get("mod_id", ""),
                blocks=spec.get("blocks", []),
                items=spec.get("items", []),
            )
        except Exception as e:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output=f"RCON connection failed: {e}",
            )

        passed = sum(1 for r in report.results if r.passed)
        failed = sum(1 for r in report.results if not r.passed)
        per_test = [
            PerTestResult(
                test_id=r.name,
                status="passed" if r.passed else "failed",
                failure_message=r.reason if not r.passed else "",
            )
            for r in report.results
        ]

        return TestRunResult(
            all_passed=report.all_passed,
            total=len(report.results),
            passed=passed,
            failed=failed,
            errors=0,
            output=report.summary(),
            per_test=per_test,
        )

    def run_single_test(
        self,
        workspace: Path,
        test_id: str,
        timeout: int = 60,
    ) -> TestRunResult:
        """Run a single RCON check by test_id (e.g. 'block_registered:mymod:ruby_block')."""
        try:
            from modfactory.verify_rcon import RCONVerifier
            from modfactory.rcon_client import RCONClient
        except ImportError:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output="modfactory SDK not installed",
            )

        verifier = RCONVerifier(self.host, self.port, self.password)
        # Parse test_id: "block_registered:mod_id:block_name"
        parts = test_id.split(":", 2)
        if len(parts) < 3:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output=f"Invalid RCON test_id format: {test_id}",
            )

        check_type, mod_id, name = parts
        try:
            with RCONClient(self.host, self.port, self.password) as rcon:
                if check_type == "block_registered":
                    result = verifier.verify_block_registered(rcon, mod_id, name)
                elif check_type == "block_placeable":
                    result = verifier.verify_block_placeable(rcon, mod_id, name)
                else:
                    return TestRunResult(
                        all_passed=False, total=0, passed=0, failed=0, errors=1,
                        output=f"Unknown RCON check type: {check_type}",
                    )
        except Exception as e:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output=f"RCON connection failed: {e}",
            )

        return TestRunResult(
            all_passed=result.passed,
            total=1,
            passed=1 if result.passed else 0,
            failed=0 if result.passed else 1,
            errors=0,
            output=f"[{'PASS' if result.passed else 'FAIL'}] {result.name}: {result.response}",
            per_test=[PerTestResult(
                test_id=result.name,
                status="passed" if result.passed else "failed",
                failure_message=result.reason if not result.passed else "",
            )],
        )

    def parse_output(self, output: str) -> TestRunResult:
        """Parse RCON verification summary output."""
        passed = 0
        failed = 0
        for line in output.split("\n"):
            if line.startswith("[PASS]"):
                passed += 1
            elif line.startswith("[FAIL]"):
                failed += 1
        total = passed + failed
        return TestRunResult(
            all_passed=total > 0 and failed == 0,
            total=total, passed=passed, failed=failed, errors=0,
            output=output,
        )

    @staticmethod
    def _load_test_spec(workspace: Path, test_dirs: list[str] | None) -> dict | None:
        """Load RCON test spec from rcon_tests.json."""
        import json
        candidates = [workspace / "rcon_tests.json"]
        if test_dirs:
            for td in test_dirs:
                candidates.append(workspace / td / "rcon_tests.json")
                # Also allow test_dirs[0] to be the JSON file itself
                candidates.append(workspace / td)

        for path in candidates:
            if path.exists() and path.suffix == ".json":
                try:
                    return json.loads(path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
        return None


# ---------------------------------------------------------------------------
# Visual test runner (Minecraft mod verification — Layer 2)
# ---------------------------------------------------------------------------


class VisualTestRunner(TestRunner):
    """Visual confirmation via screenshot + Claude Vision.

    Wraps modfactory.visual_confirm to capture screenshots from a running
    Minecraft client and analyze them with a VLM.
    """

    def __init__(self, vlm_api_key: str | None = None):
        import os
        self.vlm_api_key = vlm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def run_tests(
        self,
        workspace: Path,
        test_dirs: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        timeout: int = 300,
    ) -> TestRunResult:
        """Run visual confirmation scenarios.

        Expects workspace to contain visual_scenarios.json with scenario definitions.
        """
        try:
            from modfactory.visual_confirm import run_visual_confirm, VisualScenario
            from modfactory.report import ReportBuilder
        except ImportError:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output="modfactory SDK not installed — cannot run visual tests",
            )

        scenarios_data = self._load_scenarios(workspace)
        if not scenarios_data:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output="No visual_scenarios.json found in workspace",
            )

        scenarios = [VisualScenario(**s) for s in scenarios_data]
        report = ReportBuilder(title="ToyShop Visual Test")

        try:
            result = run_visual_confirm(
                scenarios=scenarios,
                report=report,
                vlm_api_key=self.vlm_api_key,
                timeout=timeout,
            )
        except Exception as e:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output=f"Visual confirmation failed: {e}",
            )

        per_test: list[PerTestResult] = []
        passed_count = 0
        failed_count = 0

        if result.visual:
            for vr in result.visual.results:
                per_test.append(PerTestResult(
                    test_id=f"visual:{vr.scenario_name}",
                    status="passed" if vr.passed else "failed",
                    failure_message="; ".join(vr.issues) if vr.issues else "",
                ))
                if vr.passed:
                    passed_count += 1
                else:
                    failed_count += 1

        total = passed_count + failed_count
        return TestRunResult(
            all_passed=result.passed,
            total=total,
            passed=passed_count,
            failed=failed_count,
            errors=0,
            output=result.summary(),
            per_test=per_test,
        )

    def run_single_test(
        self,
        workspace: Path,
        test_id: str,
        timeout: int = 120,
    ) -> TestRunResult:
        """Run a single visual scenario by name."""
        # Visual tests are expensive — just run all and filter
        full = self.run_tests(workspace, timeout=timeout)
        matching = [p for p in full.per_test if test_id in p.test_id]
        if not matching:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output=f"Visual scenario '{test_id}' not found",
            )
        r = matching[0]
        return TestRunResult(
            all_passed=r.status == "passed",
            total=1,
            passed=1 if r.status == "passed" else 0,
            failed=0 if r.status == "passed" else 1,
            errors=0,
            output=f"[{r.status.upper()}] {r.test_id}",
            per_test=[r],
        )

    def parse_output(self, output: str) -> TestRunResult:
        """Parse visual confirmation summary."""
        passed = output.count("[PASS]")
        failed = output.count("[FAIL]")
        total = passed + failed
        return TestRunResult(
            all_passed=total > 0 and failed == 0,
            total=total, passed=passed, failed=failed, errors=0,
            output=output,
        )

    @staticmethod
    def _load_scenarios(workspace: Path) -> list[dict] | None:
        import json
        path = workspace / "visual_scenarios.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                return None
        return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_RUNNER_REGISTRY: dict[str, type[TestRunner]] = {
    "pytest": PytestRunner,
    "junit": GradleTestRunner,
    "rcon": RconTestRunner,
    "visual": VisualTestRunner,
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
