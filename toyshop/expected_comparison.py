"""Expected comparison system for TDD pipeline.

Three assertion types:
- Hard: exact match (numeric, exception types) — must fix
- Soft: semantic comparison (docs, formatted output) — LLM evaluates
- Performance: objective metrics (time, memory) — test agent can skip

Legacy issues: tests that remain unfixable after all retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from openhands.sdk import LLM
    from toyshop.debug_hypothesis import DebugHypothesis


@dataclass
class TestVerdict:
    """Verdict for a single test."""
    test_name: str
    verdict: Literal["pass", "fail", "soft_pass", "performance_skip", "legacy_issue"]
    assertion_type: Literal["hard", "soft", "performance"]
    expected: str = ""
    actual: str = ""
    comparison_detail: str = ""
    requirement_ref: str = ""


@dataclass
class LegacyIssue:
    """A test that could not be fixed after all retries."""
    test_name: str
    description: str
    all_attempts: list[str] = field(default_factory=list)
    all_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    final_status: Literal["unfixable", "environment_issue", "spec_ambiguity"] = "unfixable"
    recommendation: str = ""

    def to_summary(self) -> str:
        parts = [
            f"## Legacy Issue: {self.test_name}",
            f"Status: {self.final_status}",
            f"Description: {self.description}",
        ]
        if self.all_attempts:
            parts.append("\nAttempts:")
            for i, a in enumerate(self.all_attempts, 1):
                parts.append(f"  {i}. {a}")
        if self.all_hypotheses:
            parts.append("\nHypotheses explored:")
            for h in self.all_hypotheses:
                status = h.get("status", "?")
                desc = h.get("description", "?")
                parts.append(f"  - [{status}] {desc}")
                if h.get("reasoning"):
                    parts.append(f"    Reasoning: {h['reasoning']}")
        if self.recommendation:
            parts.append(f"\nRecommendation: {self.recommendation}")
        return "\n".join(parts)


SOFT_EVAL_PROMPT = """You are evaluating whether an actual output satisfies a requirement.

## Requirement
{requirement}

## Design Context
{design_context}

## Actual Output
{actual_output}

## Task
Evaluate whether the actual output satisfies the requirement's intent.
Consider:
- Does it meet the functional requirement?
- Is the output format acceptable?
- Are there any significant deviations?

Respond with EXACTLY one of:
- PASS: fully satisfies the requirement
- SOFT_PASS: mostly satisfies with minor deviations that are acceptable
- FAIL: does not satisfy the requirement

Then explain your reasoning in 1-2 sentences.

Format: VERDICT: <PASS|SOFT_PASS|FAIL>
Reason: <explanation>
"""


def evaluate_soft_assertion(
    requirement: str,
    actual_output: str,
    design_context: str,
    llm: "LLM",
) -> TestVerdict:
    """Use LLM to evaluate whether actual output satisfies a soft requirement."""
    prompt = SOFT_EVAL_PROMPT.format(
        requirement=requirement,
        design_context=design_context,
        actual_output=actual_output,
    )

    response = llm.completion(
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content or ""

    # Parse verdict
    verdict = "fail"
    if "VERDICT: PASS" in content.upper():
        verdict = "pass"
    elif "VERDICT: SOFT_PASS" in content.upper():
        verdict = "soft_pass"

    # Extract reason
    reason = ""
    for line in content.split("\n"):
        if line.strip().lower().startswith("reason:"):
            reason = line.split(":", 1)[1].strip()
            break

    return TestVerdict(
        test_name="",  # caller fills this in
        verdict=verdict,  # type: ignore
        assertion_type="soft",
        expected=requirement,
        actual=actual_output,
        comparison_detail=reason,
    )


def classify_test_failure(
    test_name: str,
    failure_output: str,
) -> Literal["hard", "soft", "performance"]:
    """Heuristic classification of test failure type.

    - Performance: test name contains 'perf', 'benchmark', 'timing', 'memory'
    - Soft: test name contains 'doc', 'format', 'output', 'display', 'render'
    - Hard: everything else
    """
    name_lower = test_name.lower()
    if any(kw in name_lower for kw in ("perf", "benchmark", "timing", "memory", "speed")):
        return "performance"
    if any(kw in name_lower for kw in ("doc", "format", "output", "display", "render", "template")):
        return "soft"
    return "hard"


def mark_as_legacy(
    test_name: str,
    description: str,
    attempts: list[str],
    hypotheses: list["DebugHypothesis"],
    recommendation: str = "",
) -> LegacyIssue:
    """Create a legacy issue record for an unfixable test."""
    hyp_dicts = []
    for h in hypotheses:
        hyp_dicts.append({
            "id": h.id,
            "description": h.description,
            "status": h.status,
            "reasoning": h.reasoning,
            "target_file": h.target_file,
            "target_lines": h.target_lines,
        })

    return LegacyIssue(
        test_name=test_name,
        description=description,
        all_attempts=attempts,
        all_hypotheses=hyp_dicts,
        recommendation=recommendation or "Requires manual investigation",
    )
