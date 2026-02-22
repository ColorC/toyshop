"""Fault localization using Spectrum-Based Fault Localization (SBFL).

Uses pytest-cov with per-test coverage contexts to collect line-level coverage,
then applies the Ochiai formula to rank suspicious lines.

Ochiai(line) = ef / sqrt(total_failed * (ef + ep))
  ef = number of failing tests that cover this line
  ep = number of passing tests that cover this line
"""

from __future__ import annotations

import math
import re
import sqlite3
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

from openhands.sdk.tool import (
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.sdk.tool.schema import Action, Observation

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


@dataclass
class SuspiciousLine:
    """A line of code ranked by suspiciousness."""
    file: str
    line: int
    score: float
    covered_by_failing: int
    covered_by_passing: int


# =============================================================================
# Core fault localizer
# =============================================================================

class FaultLocalizer:
    """Run tests with coverage and rank suspicious lines using Ochiai SBFL."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run_with_coverage(self, test_pattern: str = "tests/") -> Path:
        """Run pytest with per-test coverage. Returns path to .coverage DB."""
        cov_file = self.workspace / ".coverage"
        # Remove old coverage data
        if cov_file.exists():
            cov_file.unlink()

        cmd = [
            "python3", "-m", "pytest",
            test_pattern,
            f"--cov={self.workspace}",
            "--cov-context=test",
            "--cov-branch",
            "--no-header", "-q",
        ]
        subprocess.run(
            cmd,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=120,
            env={
                **__import__("os").environ,
                "COVERAGE_FILE": str(cov_file),
            },
        )
        return cov_file

    def get_failing_tests(self, test_pattern: str = "tests/") -> list[str]:
        """Run pytest to identify which tests fail. Returns list of test node IDs."""
        result = subprocess.run(
            ["python3", "-m", "pytest", test_pattern, "-v", "--tb=no", "-q"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
        failing = []
        for line in result.stdout.split("\n"):
            if "FAILED" in line:
                # Extract test ID: "tests/test_foo.py::test_bar FAILED"
                parts = line.strip().split()
                if parts:
                    failing.append(parts[0])
        return failing

    def analyze(
        self, coverage_db: Path, failing_tests: list[str], top_n: int = 20
    ) -> list[SuspiciousLine]:
        """Read coverage DB and compute Ochiai scores."""
        if not coverage_db.exists():
            return []

        try:
            conn = sqlite3.connect(str(coverage_db))
            cursor = conn.cursor()

            # Get all contexts (test names)
            cursor.execute("SELECT id, context FROM context")
            contexts = {row[0]: row[1] for row in cursor.fetchall()}

            # Get all line data: file_id, context_id, line numbers
            # coverage.py stores data in 'line_bits' table with bitmap encoding
            # Simpler approach: use the coverage API
            conn.close()
        except Exception:
            return []

        # Fallback: use coverage.py Python API
        return self._analyze_with_coverage_api(coverage_db, failing_tests, top_n)

    def _analyze_with_coverage_api(
        self, coverage_db: Path, failing_tests: list[str], top_n: int
    ) -> list[SuspiciousLine]:
        """Use coverage.py API to extract per-test line data."""
        try:
            import coverage as cov_mod
        except ImportError:
            return []

        try:
            cov = cov_mod.Coverage(data_file=str(coverage_db))
            cov.load()
            data = cov.get_data()
        except Exception:
            return []

        # Normalize failing test names for matching
        failing_set = set()
        for t in failing_tests:
            failing_set.add(t)
            # Also add variants: "tests/test_foo.py::TestClass::test_method"
            if "::" in t:
                failing_set.add(t.split("::")[-1])

        # Collect per-line coverage by context
        # line -> {file, line, failing_count, passing_count}
        line_data: dict[tuple[str, int], dict[str, int]] = defaultdict(
            lambda: {"ef": 0, "ep": 0}
        )

        total_failed = len(failing_tests)
        if total_failed == 0:
            return []

        contexts = data.measured_contexts()
        for ctx in contexts:
            is_failing = any(f in ctx for f in failing_set)
            data.set_query_context(ctx)
            for filename in data.measured_files():
                # Skip test files and __pycache__
                rel = str(Path(filename).relative_to(self.workspace)) if filename.startswith(str(self.workspace)) else filename
                if "test_" in rel or "__pycache__" in rel or "/.tdd_debug/" in rel:
                    continue
                lines = data.lines(filename) or []
                for line_no in lines:
                    key = (rel, line_no)
                    if is_failing:
                        line_data[key]["ef"] += 1
                    else:
                        line_data[key]["ep"] += 1

        # Compute Ochiai scores
        results: list[SuspiciousLine] = []
        for (file, line), counts in line_data.items():
            ef = counts["ef"]
            ep = counts["ep"]
            if ef == 0:
                continue  # Not covered by any failing test
            denominator = math.sqrt(total_failed * (ef + ep))
            score = ef / denominator if denominator > 0 else 0.0
            results.append(SuspiciousLine(
                file=file,
                line=line,
                score=round(score, 4),
                covered_by_failing=ef,
                covered_by_passing=ep,
            ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_n]

    def localize(self, test_pattern: str = "tests/", top_n: int = 20) -> list[SuspiciousLine]:
        """One-shot: run coverage + analyze. Returns ranked suspicious lines."""
        failing = self.get_failing_tests(test_pattern)
        if not failing:
            return []
        cov_db = self.run_with_coverage(test_pattern)
        return self.analyze(cov_db, failing, top_n)


# =============================================================================
# Tool definition
# =============================================================================

class FaultLocalizeAction(Action):
    command: Literal["localize"]
    test_pattern: str = "tests/"
    top_n: int = 20

    @property
    def visualize(self):
        from rich.text import Text
        return Text(f"fault_localize {self.command}")


class FaultLocalizeObservation(Observation):
    pass


class FaultLocalizeExecutor(ToolExecutor):
    def __init__(self, workspace: Path):
        self.localizer = FaultLocalizer(workspace)

    def __call__(
        self, action: FaultLocalizeAction, conversation: "LocalConversation | None" = None
    ) -> FaultLocalizeObservation:
        results = self.localizer.localize(action.test_pattern, action.top_n)
        if not results:
            return FaultLocalizeObservation.from_text(
                "No suspicious lines found (all tests pass or no coverage data)"
            )
        lines = ["Suspicious lines (Ochiai SBFL ranking):"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"  {i}. {r.file}:{r.line} — score={r.score} "
                f"(failing={r.covered_by_failing}, passing={r.covered_by_passing})"
            )
        return FaultLocalizeObservation.from_text("\n".join(lines))


def _make_fault_localize_tool(
    conv_state: "ConversationState", **params: Any
) -> Sequence[ToolDefinition]:
    workspace = Path(conv_state.workspace.working_dir)
    executor = FaultLocalizeExecutor(workspace)

    class FaultLocalizeTool(ToolDefinition):
        name = "fault_localize"

        @classmethod
        def create(cls, *a: Any, **kw: Any) -> Sequence[ToolDefinition]:
            return []

    return [
        FaultLocalizeTool(
            description=(
                "Run fault localization using Spectrum-Based Fault Localization (SBFL). "
                "Runs tests with per-test coverage, then ranks code lines by suspiciousness "
                "using the Ochiai formula. Use command='localize' to get ranked results."
            ),
            action_type=FaultLocalizeAction,
            observation_type=FaultLocalizeObservation,
            executor=executor,
            annotations=ToolAnnotations(
                title="fault_localize",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
    ]


register_tool("fault_localize", _make_fault_localize_tool)
