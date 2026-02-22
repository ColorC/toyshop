"""Hypothesis debug system for TDD pipeline.

Manages structured debug hypotheses with lifecycle:
  pending → confirmed | excluded | suspicious

Debug Agent creates hypotheses, collects evidence via probes,
and produces a DebugReport for the Coding Agent.
Coding Agent can challenge hypotheses via CodingChallenge.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, asdict
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
    from toyshop.fault_localize import SuspiciousLine


# =============================================================================
# Data models
# =============================================================================

@dataclass
class ProbeEvidence:
    """Evidence collected from a diagnostic probe."""
    probe_id: str
    output: str
    interpretation: str = ""


@dataclass
class DebugHypothesis:
    """A structured debugging hypothesis."""
    id: str
    description: str
    target_file: str = ""
    target_lines: list[int] = field(default_factory=list)
    status: Literal["pending", "confirmed", "excluded", "suspicious"] = "pending"
    evidence: list[ProbeEvidence] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class DebugReport:
    """Complete debug report from Debug Agent to Coding Agent."""
    failing_tests: list[str] = field(default_factory=list)
    test_output: str = ""
    fault_localization: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[DebugHypothesis] = field(default_factory=list)
    excluded_hypotheses: list[DebugHypothesis] = field(default_factory=list)
    recommended_fix: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "DebugReport":
        data = json.loads(text)
        report = cls(
            failing_tests=data.get("failing_tests", []),
            test_output=data.get("test_output", ""),
            fault_localization=data.get("fault_localization", []),
            recommended_fix=data.get("recommended_fix", ""),
        )
        for h in data.get("hypotheses", []):
            hyp = DebugHypothesis(**{k: v for k, v in h.items() if k != "evidence"})
            hyp.evidence = [ProbeEvidence(**e) for e in h.get("evidence", [])]
            report.hypotheses.append(hyp)
        for h in data.get("excluded_hypotheses", []):
            hyp = DebugHypothesis(**{k: v for k, v in h.items() if k != "evidence"})
            hyp.evidence = [ProbeEvidence(**e) for e in h.get("evidence", [])]
            report.excluded_hypotheses.append(hyp)
        return report

    def to_prompt_text(self) -> str:
        """Format as text for inclusion in agent prompts."""
        parts = ["## Debug Report"]
        if self.failing_tests:
            parts.append(f"\nFailing tests: {', '.join(self.failing_tests)}")
        if self.fault_localization:
            parts.append("\n### Suspicious Lines (SBFL)")
            for sl in self.fault_localization[:10]:
                parts.append(f"  - {sl.get('file', '?')}:{sl.get('line', '?')} (score={sl.get('score', 0)})")
        if self.hypotheses:
            parts.append("\n### Hypotheses")
            for h in self.hypotheses:
                parts.append(f"\n**{h.id}** [{h.status}]: {h.description}")
                if h.target_file:
                    parts.append(f"  Target: {h.target_file}:{h.target_lines}")
                if h.reasoning:
                    parts.append(f"  Reasoning: {h.reasoning}")
                for e in h.evidence:
                    parts.append(f"  Evidence ({e.probe_id}): {e.output}")
                    if e.interpretation:
                        parts.append(f"    → {e.interpretation}")
        if self.excluded_hypotheses:
            parts.append("\n### Excluded Hypotheses (for reference)")
            for h in self.excluded_hypotheses:
                parts.append(f"  - {h.id}: {h.description} — {h.reasoning}")
        if self.recommended_fix:
            parts.append(f"\n### Recommended Fix\n{self.recommended_fix}")
        return "\n".join(parts)


@dataclass
class CodingChallenge:
    """Coding Agent's challenge to a debug hypothesis."""
    hypothesis_id: str
    challenge_reason: str
    evidence: str = ""
    attempted_fixes: list[str] = field(default_factory=list)


def parse_challenge_from_finish(finish_message: str) -> CodingChallenge | None:
    """Parse [CHALLENGE:hyp_id] from Coding Agent's finish message."""
    import re
    m = re.search(r"\[CHALLENGE:(hyp_\d+)\]\s*reason:\s*(.+?)(?:\n|$)", finish_message, re.IGNORECASE)
    if not m:
        return None
    hyp_id = m.group(1)
    reason = m.group(2).strip()
    # Try to extract evidence
    evidence = ""
    ev_match = re.search(r"\[EVIDENCE\]\s*(.+?)(?:\n\[|$)", finish_message, re.DOTALL)
    if ev_match:
        evidence = ev_match.group(1).strip()
    return CodingChallenge(
        hypothesis_id=hyp_id,
        challenge_reason=reason,
        evidence=evidence,
    )


# =============================================================================
# Debug Form system (v2 — structured failure analysis)
# =============================================================================

@dataclass
class DebugForm:
    """Structured failure analysis form filled by Test Agent for a single test."""
    test_id: str
    test_file: str = ""
    assertion_value: str = ""        # "expected X, got Y"
    assertion_meaning: str = ""      # semantic: what this assertion checks
    actual_situation: str = ""       # what the code actually does
    guessed_cause: str = ""          # REQUIRED hypothesis about why it fails
    surface_clues: str = ""          # observable symptoms
    log_clues: str = ""              # evidence from test output/logs
    excluded_guesses: list[str] = field(default_factory=list)
    exclusion_evidence: list[str] = field(default_factory=list)
    flagged_as_test_bug: bool = False  # Test Agent can flag its own test as suspect
    batch_pattern: str = ""          # regex pattern if this form covers multiple tests
    batch_test_ids: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Format for inclusion in Coding Agent prompt."""
        lines = [f"### {self.test_id}"]
        if self.test_file:
            lines.append(f"  File: {self.test_file}")
        if self.assertion_value:
            lines.append(f"  Assertion: {self.assertion_value}")
        if self.assertion_meaning:
            lines.append(f"  Meaning: {self.assertion_meaning}")
        if self.actual_situation:
            lines.append(f"  Actual: {self.actual_situation}")
        lines.append(f"  Guessed cause: {self.guessed_cause}")
        if self.surface_clues:
            lines.append(f"  Surface clues: {self.surface_clues}")
        if self.log_clues:
            lines.append(f"  Log clues: {self.log_clues}")
        if self.excluded_guesses:
            lines.append(f"  Excluded: {'; '.join(self.excluded_guesses)}")
        if self.exclusion_evidence:
            lines.append(f"  Exclusion evidence: {'; '.join(self.exclusion_evidence)}")
        if self.flagged_as_test_bug:
            lines.append("  ** FLAGGED AS POTENTIAL TEST BUG **")
        if self.batch_test_ids:
            lines.append(f"  Batch ({self.batch_pattern}): {', '.join(self.batch_test_ids)}")
        return "\n".join(lines)


@dataclass
class DebugFormSet:
    """Collection of DebugForms from a single analysis round."""
    forms: list[DebugForm] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Format all forms for inclusion in Coding Agent prompt."""
        if not self.forms:
            return "## Debug Forms\nNo forms submitted."
        parts = [f"## Debug Forms ({len(self.forms)} failures analyzed)"]
        for form in self.forms:
            parts.append(form.to_prompt_text())
        flagged = [f for f in self.forms if f.flagged_as_test_bug]
        if flagged:
            parts.append(f"\n** {len(flagged)} test(s) flagged as potential test bugs **")
        return "\n\n".join(parts)

    def to_json(self) -> str:
        return json.dumps([asdict(f) for f in self.forms], indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "DebugFormSet":
        data = json.loads(text)
        forms = [DebugForm(**d) for d in data]
        return cls(forms=forms)


@dataclass
class Rejection:
    """Coding Agent's rejection of Debug Forms back to Test Agent."""
    counter_evidence: str
    code_analysis: str = ""
    suggested_test_fix: str = ""


def parse_rejection_from_finish(finish_message: str) -> Rejection | None:
    """Parse [REJECT] from Coding Agent's finish message."""
    m = re.search(r"\[REJECT\]\s*reason:\s*(.+?)(?:\n|$)", finish_message, re.IGNORECASE)
    if not m:
        return None
    reason = m.group(1).strip()
    evidence = ""
    ev_match = re.search(r"\[COUNTER_EVIDENCE\]\s*(.+?)(?:\n\[|$)", finish_message, re.DOTALL)
    if ev_match:
        evidence = ev_match.group(1).strip()
    return Rejection(counter_evidence=reason, code_analysis=evidence)


# =============================================================================
# Debug Form tool (for Test Agent analyst)
# =============================================================================

class DebugFormAction(Action):
    command: Literal["fill", "batch_fill", "flag_test_bug", "list", "submit"]
    test_id: str | None = None
    test_file: str | None = None
    assertion_value: str | None = None
    assertion_meaning: str | None = None
    actual_situation: str | None = None
    guessed_cause: str | None = None
    surface_clues: str | None = None
    log_clues: str | None = None
    excluded_guesses: str | None = None      # semicolon-separated
    exclusion_evidence: str | None = None    # semicolon-separated
    batch_pattern: str | None = None         # regex for batch_fill
    batch_test_ids: str | None = None        # comma-separated test IDs

    @property
    def visualize(self):
        from rich.text import Text
        return Text(f"debug_form {self.command}")


class DebugFormObservation(Observation):
    pass


class DebugFormExecutor(ToolExecutor):
    """Executor for the debug_form_tool — manages DebugFormSet."""

    def __init__(self):
        self.form_set = DebugFormSet()

    def __call__(
        self, action: DebugFormAction, conversation: "LocalConversation | None" = None
    ) -> DebugFormObservation:
        cmd = action.command

        if cmd == "fill":
            if not action.test_id:
                return DebugFormObservation.from_text("Error: test_id required", is_error=True)
            if not action.guessed_cause:
                return DebugFormObservation.from_text("Error: guessed_cause is REQUIRED", is_error=True)
            form = DebugForm(
                test_id=action.test_id,
                test_file=action.test_file or "",
                assertion_value=action.assertion_value or "",
                assertion_meaning=action.assertion_meaning or "",
                actual_situation=action.actual_situation or "",
                guessed_cause=action.guessed_cause,
                surface_clues=action.surface_clues or "",
                log_clues=action.log_clues or "",
                excluded_guesses=[s.strip() for s in (action.excluded_guesses or "").split(";") if s.strip()],
                exclusion_evidence=[s.strip() for s in (action.exclusion_evidence or "").split(";") if s.strip()],
            )
            self.form_set.forms.append(form)
            return DebugFormObservation.from_text(
                f"Form filled for {form.test_id}: cause='{form.guessed_cause}'"
            )

        elif cmd == "batch_fill":
            if not action.batch_pattern:
                return DebugFormObservation.from_text("Error: batch_pattern required", is_error=True)
            if not action.guessed_cause:
                return DebugFormObservation.from_text("Error: guessed_cause is REQUIRED", is_error=True)
            test_ids = [s.strip() for s in (action.batch_test_ids or "").split(",") if s.strip()]
            if not test_ids:
                return DebugFormObservation.from_text("Error: batch_test_ids required (comma-separated)", is_error=True)
            form = DebugForm(
                test_id=f"batch:{action.batch_pattern}",
                test_file=action.test_file or "",
                assertion_value=action.assertion_value or "",
                assertion_meaning=action.assertion_meaning or "",
                actual_situation=action.actual_situation or "",
                guessed_cause=action.guessed_cause,
                surface_clues=action.surface_clues or "",
                log_clues=action.log_clues or "",
                excluded_guesses=[s.strip() for s in (action.excluded_guesses or "").split(";") if s.strip()],
                exclusion_evidence=[s.strip() for s in (action.exclusion_evidence or "").split(";") if s.strip()],
                batch_pattern=action.batch_pattern,
                batch_test_ids=test_ids,
            )
            self.form_set.forms.append(form)
            return DebugFormObservation.from_text(
                f"Batch form for pattern '{action.batch_pattern}' covering {len(test_ids)} tests: "
                f"cause='{form.guessed_cause}'"
            )

        elif cmd == "flag_test_bug":
            if not action.test_id:
                return DebugFormObservation.from_text("Error: test_id required", is_error=True)
            # Find existing form or create minimal one
            for f in self.form_set.forms:
                if f.test_id == action.test_id:
                    f.flagged_as_test_bug = True
                    return DebugFormObservation.from_text(f"Flagged {action.test_id} as potential test bug")
            # No existing form — create one with flag
            form = DebugForm(
                test_id=action.test_id,
                guessed_cause=action.guessed_cause or "test itself may be wrong",
                flagged_as_test_bug=True,
            )
            self.form_set.forms.append(form)
            return DebugFormObservation.from_text(f"Created form + flagged {action.test_id} as potential test bug")

        elif cmd == "list":
            if not self.form_set.forms:
                return DebugFormObservation.from_text("No forms filled yet")
            lines = []
            for f in self.form_set.forms:
                flag = " [TEST BUG?]" if f.flagged_as_test_bug else ""
                batch = f" (batch: {len(f.batch_test_ids)} tests)" if f.batch_test_ids else ""
                lines.append(f"  {f.test_id}: {f.guessed_cause}{flag}{batch}")
            return DebugFormObservation.from_text(f"Forms ({len(self.form_set.forms)}):\n" + "\n".join(lines))

        elif cmd == "submit":
            count = len(self.form_set.forms)
            flagged = sum(1 for f in self.form_set.forms if f.flagged_as_test_bug)
            return DebugFormObservation.from_text(
                f"Submitted {count} debug forms ({flagged} flagged as test bugs).\n"
                f"Forms will be forwarded to the Coding Agent."
            )

        return DebugFormObservation.from_text(f"Unknown command: {cmd}", is_error=True)


# =============================================================================
# Debug Form tool registration
# =============================================================================

_form_executors: dict[str, DebugFormExecutor] = {}


def get_debug_form_executor(workspace: Path) -> DebugFormExecutor:
    key = str(workspace.resolve())
    if key not in _form_executors:
        _form_executors[key] = DebugFormExecutor()
    return _form_executors[key]


def reset_debug_form_executor(workspace: Path) -> None:
    """Reset the form executor for a new analysis round."""
    key = str(workspace.resolve())
    _form_executors[key] = DebugFormExecutor()


def _make_debug_form_tool(
    conv_state: "ConversationState", **params: Any
) -> Sequence[ToolDefinition]:
    workspace = Path(conv_state.workspace.working_dir)
    executor = get_debug_form_executor(workspace)

    class DebugFormTool(ToolDefinition):
        name = "debug_form_tool"

        @classmethod
        def create(cls, *a: Any, **kw: Any) -> Sequence[ToolDefinition]:
            return []

    return [
        DebugFormTool(
            description=(
                "Fill structured Debug Forms for test failures. Commands: "
                "fill (single test form — guessed_cause REQUIRED), "
                "batch_fill (regex pattern for mass failures — batch_pattern + batch_test_ids + guessed_cause REQUIRED), "
                "flag_test_bug (mark a test as potentially wrong), "
                "list (show all forms), submit (finalize forms for Coding Agent)."
            ),
            action_type=DebugFormAction,
            observation_type=DebugFormObservation,
            executor=executor,
            annotations=ToolAnnotations(
                title="debug_form_tool",
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=False,
            ),
        )
    ]


register_tool("debug_form_tool", _make_debug_form_tool)


# =============================================================================
# Hypothesis manager (in-memory, per debug session)
# =============================================================================

class HypothesisManager:
    """Manages hypotheses for a single debug session."""

    def __init__(self):
        self._counter = 0
        self.hypotheses: list[DebugHypothesis] = []

    def create(self, description: str, target_file: str = "", target_lines: list[int] | None = None) -> DebugHypothesis:
        self._counter += 1
        hyp = DebugHypothesis(
            id=f"hyp_{self._counter:03d}",
            description=description,
            target_file=target_file,
            target_lines=target_lines or [],
        )
        self.hypotheses.append(hyp)
        return hyp

    def update(self, hyp_id: str, status: str, reasoning: str = "") -> DebugHypothesis | None:
        for h in self.hypotheses:
            if h.id == hyp_id:
                h.status = status  # type: ignore
                if reasoning:
                    h.reasoning = reasoning
                return h
        return None

    def add_evidence(self, hyp_id: str, evidence: ProbeEvidence) -> bool:
        for h in self.hypotheses:
            if h.id == hyp_id:
                h.evidence.append(evidence)
                return True
        return False

    def get_report(self) -> tuple[list[DebugHypothesis], list[DebugHypothesis]]:
        """Returns (active hypotheses, excluded hypotheses)."""
        active = [h for h in self.hypotheses if h.status != "excluded"]
        excluded = [h for h in self.hypotheses if h.status == "excluded"]
        return active, excluded

    def reset(self) -> None:
        self._counter = 0
        self.hypotheses.clear()


# =============================================================================
# Tool definition
# =============================================================================

class HypothesisAction(Action):
    command: Literal["create", "update", "add_evidence", "list", "report"]
    hypothesis_id: str | None = None
    description: str | None = None
    target_file: str | None = None
    target_lines: str | None = None  # comma-separated line numbers
    status: str | None = None
    reasoning: str | None = None
    # For add_evidence
    probe_id: str | None = None
    probe_output: str | None = None
    interpretation: str | None = None

    @property
    def visualize(self):
        from rich.text import Text
        return Text(f"hypothesis {self.command}")


class HypothesisObservation(Observation):
    pass


class HypothesisExecutor(ToolExecutor):
    def __init__(self, manager: HypothesisManager):
        self.manager = manager

    def __call__(
        self, action: HypothesisAction, conversation: "LocalConversation | None" = None
    ) -> HypothesisObservation:
        cmd = action.command

        if cmd == "create":
            if not action.description:
                return HypothesisObservation.from_text("Error: description required", is_error=True)
            lines = [int(x.strip()) for x in (action.target_lines or "").split(",") if x.strip().isdigit()]
            hyp = self.manager.create(action.description, action.target_file or "", lines)
            return HypothesisObservation.from_text(
                f"Created hypothesis {hyp.id}: {hyp.description}\n"
                f"Target: {hyp.target_file}:{hyp.target_lines}"
            )

        elif cmd == "update":
            if not action.hypothesis_id or not action.status:
                return HypothesisObservation.from_text("Error: hypothesis_id and status required", is_error=True)
            hyp = self.manager.update(action.hypothesis_id, action.status, action.reasoning or "")
            if not hyp:
                return HypothesisObservation.from_text(f"Hypothesis {action.hypothesis_id} not found", is_error=True)
            return HypothesisObservation.from_text(
                f"Updated {hyp.id} → status={hyp.status}, reasoning={hyp.reasoning}"
            )

        elif cmd == "add_evidence":
            if not action.hypothesis_id or not action.probe_id:
                return HypothesisObservation.from_text("Error: hypothesis_id and probe_id required", is_error=True)
            ev = ProbeEvidence(
                probe_id=action.probe_id,
                output=action.probe_output or "",
                interpretation=action.interpretation or "",
            )
            ok = self.manager.add_evidence(action.hypothesis_id, ev)
            if not ok:
                return HypothesisObservation.from_text(f"Hypothesis {action.hypothesis_id} not found", is_error=True)
            return HypothesisObservation.from_text(f"Added evidence from {ev.probe_id} to {action.hypothesis_id}")

        elif cmd == "list":
            if not self.manager.hypotheses:
                return HypothesisObservation.from_text("No hypotheses created yet")
            lines = []
            for h in self.manager.hypotheses:
                lines.append(f"  {h.id} [{h.status}]: {h.description}")
            return HypothesisObservation.from_text("Hypotheses:\n" + "\n".join(lines))

        elif cmd == "report":
            active, excluded = self.manager.get_report()
            report = DebugReport(hypotheses=active, excluded_hypotheses=excluded)
            return HypothesisObservation.from_text(report.to_prompt_text())

        return HypothesisObservation.from_text(f"Unknown command: {cmd}", is_error=True)


# =============================================================================
# Tool registration
# =============================================================================

_managers: dict[str, HypothesisManager] = {}


def get_hypothesis_manager(workspace: Path) -> HypothesisManager:
    key = str(workspace.resolve())
    if key not in _managers:
        _managers[key] = HypothesisManager()
    return _managers[key]


def _make_hypothesis_tool(
    conv_state: "ConversationState", **params: Any
) -> Sequence[ToolDefinition]:
    workspace = Path(conv_state.workspace.working_dir)
    manager = get_hypothesis_manager(workspace)
    executor = HypothesisExecutor(manager)

    class HypothesisTool(ToolDefinition):
        name = "hypothesis_tool"

        @classmethod
        def create(cls, *a: Any, **kw: Any) -> Sequence[ToolDefinition]:
            return []

    return [
        HypothesisTool(
            description=(
                "Manage debug hypotheses. Commands: "
                "create (new hypothesis), update (change status to confirmed/excluded/suspicious), "
                "add_evidence (attach probe evidence), list (show all), report (generate debug report)."
            ),
            action_type=HypothesisAction,
            observation_type=HypothesisObservation,
            executor=executor,
            annotations=ToolAnnotations(
                title="hypothesis_tool",
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=False,
            ),
        )
    ]


register_tool("hypothesis_tool", _make_hypothesis_tool)
