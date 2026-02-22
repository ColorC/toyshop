"""Diagnostic probe system for TDD debug pipeline.

Probes are controlled diagnostic statements inserted into source code:
- trace probe: logs expression values, program continues normally
- halt probe: logs expression values then exits with code 99 (one-shot breakpoint)

All probes write to stderr with unique markers: [PROBE:<id>] or [PROBE:<id>:HALT]
ProbeInstrumentor backs up files before modification and restores on cleanup.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
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


# =============================================================================
# Data models
# =============================================================================

@dataclass
class DiagnosticProbe:
    """A single diagnostic probe to be inserted into source code."""
    id: str                                    # probe_001, probe_002...
    probe_type: Literal["trace", "halt"]
    file_path: str                             # target file (relative to workspace)
    line_number: int                           # insert BEFORE this line (1-based)
    expression: str                            # Python expression to capture
    hypothesis_id: str | None = None           # linked hypothesis
    inserted: bool = False


# =============================================================================
# Probe instrumentor
# =============================================================================

_PROBE_COUNTER = 0


def _next_probe_id() -> str:
    global _PROBE_COUNTER
    _PROBE_COUNTER += 1
    return f"probe_{_PROBE_COUNTER:03d}"


def reset_probe_counter() -> None:
    global _PROBE_COUNTER
    _PROBE_COUNTER = 0


class ProbeInstrumentor:
    """Insert/remove diagnostic probes in source files via line-based text insertion."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.probes: list[DiagnosticProbe] = []
        self._file_backups: dict[str, str] = {}  # abs_path -> original content

    def insert_probe(self, probe: DiagnosticProbe) -> str:
        """Insert a probe into the target file. Returns the probe code inserted."""
        abs_path = str((self.workspace / probe.file_path).resolve())

        # Backup original content on first modification
        if abs_path not in self._file_backups:
            content = Path(abs_path).read_text(encoding="utf-8")
            self._file_backups[abs_path] = content

        # Read current content (may already have probes)
        current = Path(abs_path).read_text(encoding="utf-8")
        lines = current.split("\n")

        # Build probe code (single line to minimize disruption)
        if probe.probe_type == "trace":
            probe_code = (
                f"import sys as __probe_sys; __probe_sys.stderr.write("
                f"'[PROBE:{probe.id}] ' + str({probe.expression}) + '\\n')"
            )
        else:  # halt
            probe_code = (
                f"import sys as __probe_sys; __probe_sys.stderr.write("
                f"'[PROBE:{probe.id}:HALT] ' + str({probe.expression}) + '\\n'"
                f"); __probe_sys.exit(99)"
            )

        # Determine indentation from target line
        target_idx = probe.line_number - 1  # 0-based
        if 0 <= target_idx < len(lines):
            target_line = lines[target_idx]
            indent = len(target_line) - len(target_line.lstrip())
            probe_line = " " * indent + probe_code
        else:
            probe_line = probe_code

        # Insert before target line
        if 0 <= target_idx <= len(lines):
            lines.insert(target_idx, probe_line)
        else:
            lines.append(probe_line)

        Path(abs_path).write_text("\n".join(lines), encoding="utf-8")
        probe.inserted = True
        self.probes.append(probe)
        return probe_code

    def remove_all_probes(self) -> int:
        """Restore all files from backups. Returns number of files restored."""
        count = 0
        for abs_path, original in self._file_backups.items():
            Path(abs_path).write_text(original, encoding="utf-8")
            count += 1
        self._file_backups.clear()
        for p in self.probes:
            p.inserted = False
        self.probes.clear()
        return count

    def list_probes(self) -> list[dict[str, Any]]:
        """Return probe info as dicts."""
        return [
            {
                "id": p.id,
                "type": p.probe_type,
                "file": p.file_path,
                "line": p.line_number,
                "expression": p.expression,
                "hypothesis_id": p.hypothesis_id,
                "inserted": p.inserted,
            }
            for p in self.probes
        ]

    @staticmethod
    def collect_probe_output(output: str) -> dict[str, str]:
        """Extract probe outputs from stderr/combined output.

        Returns dict: probe_id -> captured output text.
        """
        results: dict[str, str] = {}
        pattern = re.compile(r"\[PROBE:(probe_\d+)(?::HALT)?\]\s*(.+)")
        for line in output.split("\n"):
            m = pattern.search(line)
            if m:
                probe_id = m.group(1)
                value = m.group(2).strip()
                results[probe_id] = value
        return results


# =============================================================================
# Tool definition for openhands-sdk
# =============================================================================

class ProbeAction(Action):
    """Action schema for the probe tool."""
    command: Literal["insert_trace", "insert_halt", "remove_all", "list", "collect"]
    file_path: str | None = None
    line_number: int | None = None
    expression: str | None = None
    hypothesis_id: str | None = None
    output_text: str | None = None  # for "collect" command

    @property
    def visualize(self):
        from rich.text import Text
        return Text(f"probe {self.command}")


class ProbeObservation(Observation):
    """Observation from probe tool execution."""
    pass


class ProbeExecutor(ToolExecutor):
    """Executor that manages probe insertion/removal."""

    def __init__(self, instrumentor: ProbeInstrumentor):
        self.instrumentor = instrumentor

    def __call__(
        self, action: ProbeAction, conversation: "LocalConversation | None" = None
    ) -> ProbeObservation:
        cmd = action.command

        if cmd in ("insert_trace", "insert_halt"):
            if not action.file_path or not action.line_number or not action.expression:
                return ProbeObservation.from_text(
                    "Error: file_path, line_number, and expression are required for insert",
                    is_error=True,
                )
            probe_type = "trace" if cmd == "insert_trace" else "halt"
            probe = DiagnosticProbe(
                id=_next_probe_id(),
                probe_type=probe_type,
                file_path=action.file_path,
                line_number=action.line_number,
                expression=action.expression,
                hypothesis_id=action.hypothesis_id,
            )
            code = self.instrumentor.insert_probe(probe)
            return ProbeObservation.from_text(
                f"Inserted {probe_type} probe {probe.id} at {action.file_path}:{action.line_number}\n"
                f"Code: {code}"
            )

        elif cmd == "remove_all":
            count = self.instrumentor.remove_all_probes()
            reset_probe_counter()
            return ProbeObservation.from_text(f"Removed all probes, restored {count} files")

        elif cmd == "list":
            probes = self.instrumentor.list_probes()
            if not probes:
                return ProbeObservation.from_text("No probes currently inserted")
            lines = [f"  {p['id']}: {p['type']} at {p['file']}:{p['line']} — {p['expression']}" for p in probes]
            return ProbeObservation.from_text("Active probes:\n" + "\n".join(lines))

        elif cmd == "collect":
            if not action.output_text:
                return ProbeObservation.from_text(
                    "Error: output_text is required for collect", is_error=True
                )
            results = ProbeInstrumentor.collect_probe_output(action.output_text)
            if not results:
                return ProbeObservation.from_text("No probe output found in the provided text")
            lines = [f"  {pid}: {val}" for pid, val in results.items()]
            return ProbeObservation.from_text("Probe results:\n" + "\n".join(lines))

        return ProbeObservation.from_text(f"Unknown command: {cmd}", is_error=True)


# =============================================================================
# Tool registration
# =============================================================================

# Global instrumentor — created per-workspace in the factory
_instrumentors: dict[str, ProbeInstrumentor] = {}


def get_instrumentor(workspace: Path) -> ProbeInstrumentor:
    """Get or create a ProbeInstrumentor for a workspace."""
    key = str(workspace.resolve())
    if key not in _instrumentors:
        _instrumentors[key] = ProbeInstrumentor(workspace)
    return _instrumentors[key]


class ProbeToolDefinition(ToolDefinition):
    """Probe tool definition for openhands-sdk."""
    name = "probe_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None, **params: Any) -> Sequence[ToolDefinition]:
        workspace = Path(conv_state.workspace.working_dir) if conv_state else Path(".")
        instrumentor = get_instrumentor(workspace)
        executor = ProbeExecutor(instrumentor)
        return [
            cls(
                description=(
                    "Insert diagnostic probes into source code for debugging. "
                    "Commands: insert_trace (non-interrupting log), insert_halt (breakpoint), "
                    "remove_all (restore files), list (show probes), collect (parse probe output)."
                ),
                action_type=ProbeAction,
                observation_type=ProbeObservation,
                executor=executor,
                annotations=ToolAnnotations(
                    title="probe_tool",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


def _make_probe_tool(
    conv_state: "ConversationState", **params: Any
) -> Sequence[ToolDefinition]:
    return ProbeToolDefinition.create(conv_state, **params)


register_tool("probe_tool", _make_probe_tool)
