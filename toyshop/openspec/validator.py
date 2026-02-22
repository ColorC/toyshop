"""OpenSpec document validators.

Validate OpenSpec documents for completeness and correctness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toyshop.openspec.types import (
        OpenSpecProposal,
        OpenSpecDesign,
        OpenSpecTasks,
        OpenSpecSpec,
    )


@dataclass
class ValidationError:
    path: str
    message: str


@dataclass
class ValidationWarning:
    path: str
    message: str


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Proposal Validator
# ---------------------------------------------------------------------------


def validate_proposal(proposal: "OpenSpecProposal") -> ValidationResult:
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    # Required fields
    if "Proposal" not in proposal.format:
        errors.append(ValidationError("format", "Invalid format, expected OpenSpec v1.0 Proposal"))

    if not proposal.project_name or not proposal.project_name.strip():
        errors.append(ValidationError("projectName", "Project name is required"))

    if not proposal.background or not proposal.background.strip():
        errors.append(ValidationError("background", "Background is required"))

    if not proposal.problem or not proposal.problem.strip():
        errors.append(ValidationError("problem", "Problem statement is required"))

    if not proposal.goals:
        errors.append(ValidationError("goals", "At least one goal is required"))

    # Warnings
    if not proposal.capabilities:
        warnings.append(ValidationWarning("capabilities", "No capabilities defined"))

    if not proposal.timeline or not proposal.timeline.strip():
        warnings.append(ValidationWarning("timeline", "Timeline is not defined"))

    # Validate capability priorities
    valid_priorities = {"must", "should", "could", "wont"}
    for i, cap in enumerate(proposal.capabilities):
        if cap.priority.value not in valid_priorities:
            errors.append(ValidationError(
                f"capabilities[{i}].priority",
                f"Invalid priority: {cap.priority}. Must be one of: {', '.join(valid_priorities)}"
            ))

    # Validate risk severities
    valid_severities = {"low", "medium", "high", "critical"}
    for i, risk in enumerate(proposal.risks):
        if risk.severity.value not in valid_severities:
            errors.append(ValidationError(
                f"risks[{i}].severity",
                f"Invalid severity: {risk.severity}. Must be one of: {', '.join(valid_severities)}"
            ))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Design Validator
# ---------------------------------------------------------------------------


def validate_design(design: "OpenSpecDesign") -> ValidationResult:
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    # Required fields
    if "Design" not in design.format:
        errors.append(ValidationError("format", "Invalid format, expected OpenSpec v1.0 Design"))

    if not design.requirement or not design.requirement.strip():
        errors.append(ValidationError("requirement", "Requirement is required"))

    if not design.modules:
        errors.append(ValidationError("modules", "At least one module is required"))

    # Validate modules
    module_ids: set[str] = set()
    for i, mod in enumerate(design.modules):
        if not mod.id or not mod.id.strip():
            errors.append(ValidationError(f"modules[{i}].id", "Module ID is required"))
        elif mod.id in module_ids:
            errors.append(ValidationError(f"modules[{i}].id", f"Duplicate module ID: {mod.id}"))
        else:
            module_ids.add(mod.id)

        if not mod.name or not mod.name.strip():
            errors.append(ValidationError(f"modules[{i}].name", "Module name is required"))

        if not mod.file_path or not mod.file_path.strip():
            warnings.append(ValidationWarning(f"modules[{i}].filePath", "File path is not defined"))

    # Validate interfaces
    for i, intf in enumerate(design.interfaces):
        if not intf.id or not intf.id.strip():
            errors.append(ValidationError(f"interfaces[{i}].id", "Interface ID is required"))

        if not intf.module_id or not intf.module_id.strip():
            errors.append(ValidationError(f"interfaces[{i}].moduleId", "Interface must reference a module"))
        elif intf.module_id not in module_ids:
            errors.append(ValidationError(
                f"interfaces[{i}].moduleId",
                f"Interface references non-existent module: {intf.module_id}"
            ))

        valid_types = {"api", "class", "function", "type"}
        if intf.type.value not in valid_types:
            errors.append(ValidationError(
                f"interfaces[{i}].type",
                f"Invalid type: {intf.type}. Must be one of: {', '.join(valid_types)}"
            ))

    # Warnings
    if not design.decisions:
        warnings.append(ValidationWarning("decisions", "No architecture decisions documented"))

    if not design.goals:
        warnings.append(ValidationWarning("goals", "No design goals defined"))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Tasks Validator
# ---------------------------------------------------------------------------


def validate_tasks(tasks: "OpenSpecTasks") -> ValidationResult:
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    # Required fields
    if "Tasks" not in tasks.format:
        errors.append(ValidationError("format", "Invalid format, expected OpenSpec v1.0 Tasks"))

    if not tasks.tasks:
        errors.append(ValidationError("tasks", "At least one task is required"))
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # Validate task IDs
    task_ids: set[str] = set()
    valid_statuses = {"pending", "in_progress", "completed", "blocked"}

    for i, task in enumerate(tasks.tasks):
        if not task.id or not task.id.strip():
            errors.append(ValidationError(f"tasks[{i}].id", "Task ID is required"))
        else:
            # Validate X.Y format
            if not re.match(r"^\d+(\.\d+)?$", task.id):
                errors.append(ValidationError(
                    f"tasks[{i}].id",
                    f"Invalid task ID format: {task.id}. Expected X.Y format (e.g., \"1.2\")"
                ))
            elif task.id in task_ids:
                errors.append(ValidationError(f"tasks[{i}].id", f"Duplicate task ID: {task.id}"))
            else:
                task_ids.add(task.id)

        if not task.title or not task.title.strip():
            errors.append(ValidationError(f"tasks[{i}].title", "Task title is required"))

        if task.status.value not in valid_statuses:
            errors.append(ValidationError(
                f"tasks[{i}].status",
                f"Invalid status: {task.status}. Must be one of: {', '.join(valid_statuses)}"
            ))

        # Validate dependencies
        for dep_id in task.dependencies:
            if dep_id not in task_ids and not any(t.id == dep_id for t in tasks.tasks):
                warnings.append(ValidationWarning(
                    f"tasks[{i}].dependencies",
                    f"Task references dependency that doesn't exist yet: {dep_id}"
                ))

    # Warnings
    pending_count = sum(1 for t in tasks.tasks if t.status.value == "pending")
    if pending_count == len(tasks.tasks):
        warnings.append(ValidationWarning("tasks", "All tasks are pending"))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# Need to import re for the regex
import re


# ---------------------------------------------------------------------------
# Spec Validator
# ---------------------------------------------------------------------------


def validate_spec(spec: "OpenSpecSpec") -> ValidationResult:
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    # Required fields
    if "Spec" not in spec.format:
        errors.append(ValidationError("format", "Invalid format, expected OpenSpec v1.0 Spec"))

    if not spec.scenarios:
        errors.append(ValidationError("scenarios", "At least one scenario is required"))
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # Validate scenarios
    scenario_ids: set[str] = set()

    for i, scenario in enumerate(spec.scenarios):
        if not scenario.id or not scenario.id.strip():
            errors.append(ValidationError(f"scenarios[{i}].id", "Scenario ID is required"))
        elif scenario.id in scenario_ids:
            errors.append(ValidationError(f"scenarios[{i}].id", f"Duplicate scenario ID: {scenario.id}"))
        else:
            scenario_ids.add(scenario.id)

        if not scenario.name or not scenario.name.strip():
            errors.append(ValidationError(f"scenarios[{i}].name", "Scenario name is required"))

        if not scenario.given or not scenario.given.strip():
            errors.append(ValidationError(f"scenarios[{i}].given", "GIVEN clause is required"))

        if not scenario.when or not scenario.when.strip():
            errors.append(ValidationError(f"scenarios[{i}].when", "WHEN clause is required"))

        if not scenario.then or not scenario.then.strip():
            errors.append(ValidationError(f"scenarios[{i}].then", "THEN clause is required"))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Bundle Validator
# ---------------------------------------------------------------------------


@dataclass
class BundleValidationResult:
    valid: bool
    proposal: ValidationResult
    design: ValidationResult
    tasks: ValidationResult
    spec: ValidationResult


def validate_bundle(bundle: dict) -> BundleValidationResult:
    """Validate a complete OpenSpec bundle."""
    proposal_result = validate_proposal(bundle["proposal"])
    design_result = validate_design(bundle["design"])
    tasks_result = validate_tasks(bundle["tasks"])
    spec_result = validate_spec(bundle["spec"])

    return BundleValidationResult(
        valid=(
            proposal_result.valid
            and design_result.valid
            and tasks_result.valid
            and spec_result.valid
        ),
        proposal=proposal_result,
        design=design_result,
        tasks=tasks_result,
        spec=spec_result,
    )
