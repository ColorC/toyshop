"""OpenSpec Bridge - Integration with OpenSpec CLI.

This module provides integration with the OpenSpec library at /home/dministrator/work/OpenSpec.
OpenSpec is a TypeScript project, so we bridge via its CLI JSON interface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ValidationResult:
    """Result of OpenSpec validation."""
    valid: bool
    errors: list[str]
    warnings: list[str]
    raw_report: dict[str, Any] | None = None


class OpenSpecBridge:
    """Bridge to OpenSpec CLI for spec-driven development."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)
        self._openspec_cli = self._find_openspec_cli()

    def _find_openspec_cli(self) -> str | None:
        """Find the openspec CLI binary."""
        # Check if openspec is in PATH
        openspec = shutil.which("openspec")
        if openspec:
            return openspec

        # Check common locations
        candidates = [
            Path.home() / ".local" / "bin" / "openspec",
            Path("/usr/local/bin/openspec"),
            Path("/usr/bin/openspec"),
            # Check npm global
            Path.home() / ".npm-global" / "bin" / "openspec",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        return None

    def is_available(self) -> bool:
        """Check if OpenSpec CLI is available."""
        return self._openspec_cli is not None

    def _run_cli(self, args: list[str], check: bool = True, json_output: bool = True) -> dict[str, Any] | None:
        """Run OpenSpec CLI with optional JSON output."""
        if not self._openspec_cli:
            return None

        cmd = [self._openspec_cli] + args
        if json_output:
            cmd.append("--json")
        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=60,
                check=check,
            )
            if json_output and result.stdout:
                return json.loads(result.stdout)
            elif not json_output:
                return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
            return None
        except subprocess.TimeoutExpired:
            return None
        except json.JSONDecodeError:
            return None
        except subprocess.CalledProcessError as e:
            if e.stdout:
                try:
                    return json.loads(e.stdout)
                except json.JSONDecodeError:
                    pass
            return None

    def init_project(self, name: str) -> bool:
        """Initialize an OpenSpec project in the workspace."""
        if not self._openspec_cli:
            return False

        try:
            subprocess.run(
                [self._openspec_cli, "init", name],
                cwd=self.workspace,
                capture_output=True,
                check=True,
                timeout=30,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def status(self) -> dict[str, Any] | None:
        """Get OpenSpec project status."""
        return self._run_cli(["status"])

    def list_artifacts(self) -> list[dict[str, Any]]:
        """List all artifacts in the project."""
        result = self._run_cli(["list"])
        if result and "artifacts" in result:
            return result["artifacts"]
        return []

    def get_schema(self) -> dict[str, Any] | None:
        """Get the current workflow schema."""
        return self._run_cli(["schemas"])

    def validate(self, strict: bool = False) -> ValidationResult:
        """Validate the OpenSpec project using native CLI validation.

        OpenSpec CLI uses Zod schemas for validation:
        - Requirements must contain SHALL/MUST keywords
        - Requirements must have at least one scenario
        - Specs must have name, overview, requirements

        Args:
            strict: If True, warnings are treated as errors

        Returns:
            ValidationResult with valid flag, errors, and warnings
        """
        if not self._openspec_cli:
            return ValidationResult(
                valid=False,
                errors=["OpenSpec CLI not found"],
                warnings=[],
            )

        args = ["validate"]
        if strict:
            args.append("--strict")

        result = self._run_cli(args, check=False)

        if result is None:
            return ValidationResult(
                valid=False,
                errors=["Failed to run OpenSpec validation"],
                warnings=[],
            )

        # Parse validation report
        errors = []
        warnings = []
        valid = result.get("valid", False)

        for issue in result.get("issues", []):
            level = issue.get("level", "ERROR")
            message = issue.get("message", "")
            path = issue.get("path", "")
            full_msg = f"{path}: {message}" if path else message

            if level == "ERROR":
                errors.append(full_msg)
            elif level == "WARNING":
                warnings.append(full_msg)

        return ValidationResult(
            valid=valid,
            errors=errors,
            warnings=warnings,
            raw_report=result,
        )

    def validate_spec(self, spec_name: str, strict: bool = False) -> ValidationResult:
        """Validate a specific spec."""
        if not self._openspec_cli:
            return ValidationResult(
                valid=False,
                errors=["OpenSpec CLI not found"],
                warnings=[],
            )

        args = ["validate", spec_name]
        if strict:
            args.append("--strict")

        result = self._run_cli(args, check=False)

        if result is None:
            return ValidationResult(
                valid=False,
                errors=[f"Failed to validate spec: {spec_name}"],
                warnings=[],
            )

        errors = []
        warnings = []
        valid = result.get("valid", False)

        for issue in result.get("issues", []):
            level = issue.get("level", "ERROR")
            message = issue.get("message", "")
            path = issue.get("path", "")
            full_msg = f"{path}: {message}" if path else message

            if level == "ERROR":
                errors.append(full_msg)
            elif level == "WARNING":
                warnings.append(full_msg)

        return ValidationResult(
            valid=valid,
            errors=errors,
            warnings=warnings,
            raw_report=result,
        )

    def get_instructions(self, artifact: str, change: str | None = None) -> dict[str, Any] | None:
        """Get instructions for generating an artifact."""
        args = ["instructions", artifact]
        if change:
            args.extend(["--change", change])
        return self._run_cli(args)


def create_openspec_artifact(
    workspace: str | Path,
    artifact_type: str,
    content: dict[str, Any],
) -> Path | None:
    """Create an OpenSpec artifact file in the workspace.

    Since OpenSpec uses standard file formats, we can write directly.

    Args:
        workspace: Workspace directory
        artifact_type: Type of artifact (proposal, design, tasks, spec)
        content: Artifact content

    Returns:
        Path to the created file, or None on failure
    """
    workspace = Path(workspace)
    openspec_dir = workspace / "openspec"
    openspec_dir.mkdir(parents=True, exist_ok=True)

    # Map artifact types to filenames
    filename_map = {
        "proposal": "proposal.md",
        "design": "design.md",
        "tasks": "tasks.md",
        "spec": "spec.md",
    }

    filename = filename_map.get(artifact_type)
    if not filename:
        return None

    filepath = openspec_dir / filename

    # Convert content to markdown
    markdown = artifact_to_markdown(artifact_type, content)
    if not markdown:
        return None

    filepath.write_text(markdown)
    return filepath


def artifact_to_markdown(artifact_type: str, content: dict[str, Any]) -> str | None:
    """Convert artifact content to Markdown format.

    This produces OpenSpec-compatible markdown files.
    """
    if artifact_type == "proposal":
        return _proposal_to_markdown(content)
    elif artifact_type == "design":
        return _design_to_markdown(content)
    elif artifact_type == "tasks":
        return _tasks_to_markdown(content)
    elif artifact_type == "spec":
        return _spec_to_markdown(content)
    return None


def _proposal_to_markdown(content: dict[str, Any]) -> str:
    """Convert proposal content to markdown."""
    lines = ["# Proposal", ""]

    if "projectName" in content:
        lines.append(f"## {content['projectName']}")
        lines.append("")

    lines.append("### Background")
    lines.append(content.get("background", ""))
    lines.append("")

    lines.append("### Problem")
    lines.append(content.get("problem", ""))
    lines.append("")

    if content.get("goals"):
        lines.append("### Goals")
        for goal in content["goals"]:
            lines.append(f"- {goal}")
        lines.append("")

    if content.get("nonGoals"):
        lines.append("### Non-Goals")
        for ng in content["nonGoals"]:
            lines.append(f"- {ng}")
        lines.append("")

    if content.get("capabilities"):
        lines.append("### Capabilities")
        for cap in content["capabilities"]:
            priority = cap.get("priority", "should")
            lines.append(f"- **[{priority.upper()}]** {cap.get('name', '')}: {cap.get('description', '')}")
        lines.append("")

    if content.get("risks"):
        lines.append("### Risks")
        for risk in content["risks"]:
            severity = risk.get("severity", "medium")
            lines.append(f"- **[{severity.upper()}]** {risk.get('description', '')}")
            lines.append(f"  - Mitigation: {risk.get('mitigation', '')}")
        lines.append("")

    return "\n".join(lines)


def _design_to_markdown(content: dict[str, Any]) -> str:
    """Convert design content to markdown."""
    lines = ["# Design", ""]

    if content.get("requirement"):
        lines.append("### Requirement")
        lines.append(content["requirement"])
        lines.append("")

    if content.get("constraints"):
        lines.append("### Constraints")
        for c in content["constraints"]:
            lines.append(f"- {c}")
        lines.append("")

    if content.get("decisions"):
        lines.append("### Architecture Decisions")
        for decision in content["decisions"]:
            lines.append(f"#### {decision.get('id', '')}: {decision.get('title', '')}")
            lines.append(f"**Context:** {decision.get('context', '')}")
            lines.append(f"**Decision:** {decision.get('decision', '')}")
            lines.append(f"**Consequences:** {decision.get('consequences', '')}")
            lines.append("")

    if content.get("modules"):
        lines.append("### Modules")
        for mod in content["modules"]:
            lines.append(f"#### {mod.get('name', '')}")
            lines.append(f"{mod.get('description', '')}")
            lines.append(f"- **Path:** `{mod.get('filePath', '')}`")
            if mod.get("responsibilities"):
                lines.append("- **Responsibilities:**")
                for r in mod["responsibilities"]:
                    lines.append(f"  - {r}")
            if mod.get("dependencies"):
                lines.append(f"- **Dependencies:** {', '.join(mod['dependencies'])}")
            lines.append("")

    if content.get("interfaces"):
        lines.append("### Interfaces")
        for iface in content["interfaces"]:
            lines.append(f"#### {iface.get('name', '')}")
            lines.append(f"`{iface.get('signature', '')}`")
            lines.append(f"{iface.get('description', '')}")
            lines.append("")

    if content.get("dataModels"):
        lines.append("### Data Models")
        for model in content["dataModels"]:
            lines.append(f"#### {model.get('name', '')}")
            if model.get("fields"):
                for field in model["fields"]:
                    required = "required" if field.get("required") else "optional"
                    lines.append(f"- `{field.get('name', '')}: {field.get('type', '')}` ({required})")
            lines.append("")

    return "\n".join(lines)


def _tasks_to_markdown(content: list[dict[str, Any]]) -> str:
    """Convert tasks content to markdown."""
    lines = ["# Tasks", ""]

    # Group by top-level task
    top_level = {}
    for task in content:
        task_id = task.get("id", "")
        parts = task_id.split(".")
        if len(parts) == 1:
            if task_id not in top_level:
                top_level[task_id] = {"task": task, "subtasks": []}
        else:
            parent = parts[0]
            if parent not in top_level:
                top_level[parent] = {"task": None, "subtasks": []}
            top_level[parent]["subtasks"].append(task)

    for task_id in sorted(top_level.keys()):
        entry = top_level[task_id]
        if entry["task"]:
            task = entry["task"]
            lines.append(f"## {task_id}. {task.get('title', '')}")
            lines.append(task.get("description", ""))
            lines.append("")
        else:
            lines.append(f"## {task_id}. (Group)")
            lines.append("")

        for subtask in entry["subtasks"]:
            sub_id = subtask.get("id", "")
            lines.append(f"### {sub_id} {subtask.get('title', '')}")
            lines.append(subtask.get("description", ""))
            if subtask.get("dependencies"):
                lines.append(f"**Dependencies:** {', '.join(subtask['dependencies'])}")
            if subtask.get("assignedModule"):
                lines.append(f"**Module:** {subtask['assignedModule']}")
            lines.append("")

    return "\n".join(lines)


def _spec_to_markdown(content: dict[str, Any]) -> str:
    """Convert spec content to markdown."""
    lines = ["# Specification", ""]

    scenarios = content.get("scenarios", [])
    for scenario in scenarios:
        lines.append(f"## {scenario.get('id', '')}: {scenario.get('name', '')}")
        lines.append(f"**Given:** {scenario.get('given', '')}")
        lines.append(f"**When:** {scenario.get('when', '')}")
        lines.append(f"**Then:** {scenario.get('then', '')}")
        lines.append("")

    return "\n".join(lines)
