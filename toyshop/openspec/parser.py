"""OpenSpec document parsers.

Parse markdown documents into structured OpenSpec objects.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from toyshop.openspec.types import (
    OpenSpecProposal,
    OpenSpecDesign,
    OpenSpecTasks,
    OpenSpecSpec,
    Priority,
    Severity,
    TaskStatus,
    InterfaceType,
    Capability,
    Risk,
    Goal,
    ArchitectureDecision,
    ModuleDefinition,
    InterfaceDefinition,
    Task,
    Scenario,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Proposal Parser
# ---------------------------------------------------------------------------


def parse_proposal(markdown: str) -> OpenSpecProposal | None:
    """Parse a proposal markdown document."""
    try:
        lines = markdown.split("\n")
        data: dict = {
            "goals": [],
            "nonGoals": [],
            "capabilities": [],
            "impactedAreas": [],
            "risks": [],
            "dependencies": [],
        }

        section = ""
        subsection = ""

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Headers
            if line.startswith("# ") and not line.startswith("## "):
                data["projectName"] = stripped[2:]
                continue

            if line.startswith("## "):
                section = stripped[3:].lower()
                subsection = ""
                continue

            if line.startswith("### "):
                subsection = stripped[4:].lower()
                continue

            # Content
            if section == "why":
                if subsection == "background":
                    data["background"] = data.get("background", "") + " " + stripped
                elif subsection == "problem":
                    data["problem"] = data.get("problem", "") + " " + stripped
                elif subsection == "goals":
                    if stripped.startswith("- "):
                        data["goals"].append(stripped[2:])
                elif subsection == "non-goals":
                    if stripped.startswith("- ") and not stripped.startswith("_"):
                        data["nonGoals"].append(stripped[2:])

            elif section == "what changes":
                if subsection == "impacted areas":
                    if stripped.startswith("- "):
                        data["impactedAreas"].append(stripped[2:])

            elif section == "impact":
                if subsection == "dependencies":
                    if stripped.startswith("- ") and not stripped.startswith("_"):
                        data["dependencies"].append(stripped[2:])
                elif subsection == "timeline":
                    data["timeline"] = data.get("timeline", "") + " " + stripped

        # Cleanup
        data["background"] = data.get("background", "").strip()
        data["problem"] = data.get("problem", "").strip()
        data["timeline"] = data.get("timeline", "").strip()

        # Validate
        if not data.get("projectName") or not data.get("background") or not data.get("problem"):
            return None

        return OpenSpecProposal(**data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Design Parser
# ---------------------------------------------------------------------------


def parse_design(markdown: str) -> OpenSpecDesign | None:
    """Parse a design markdown document."""
    try:
        data: dict = {
            "constraints": [],
            "goals": [],
            "decisions": [],
            "modules": [],
            "interfaces": [],
            "dataModels": [],
            "apiEndpoints": [],
            "risks": [],
            "tradeoffs": [],
        }

        lines = markdown.split("\n")
        section = ""
        current_module: dict | None = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if line.startswith("## "):
                section = stripped[3:].lower()
                continue

            if section == "context":
                if stripped.lower().startswith("### requirement"):
                    pass  # Next lines will be requirement
                elif stripped.startswith("- "):
                    data["constraints"].append(stripped[2:])
                elif not stripped.startswith("#") and not data.get("requirement"):
                    data["requirement"] = data.get("requirement", "") + " " + stripped

            elif section == "architecture":
                if line.startswith("#### "):
                    # Save previous module
                    if current_module and current_module.get("id") and current_module.get("name"):
                        data["modules"].append({
                            "id": current_module["id"],
                            "name": current_module["name"],
                            "description": current_module.get("description", ""),
                            "responsibilities": current_module.get("responsibilities", []),
                            "dependencies": current_module.get("dependencies", []),
                            "filePath": current_module.get("filePath", ""),
                        })
                    # Start new module
                    m = re.match(r"^(.+?)\s*\(`(.+?)`\)$", stripped)
                    if m:
                        current_module = {
                            "name": m.group(1),
                            "id": m.group(2),
                            "responsibilities": [],
                            "dependencies": [],
                        }
                elif current_module:
                    if stripped.startswith("- **File:**"):
                        fm = re.search(r"`(.+?)`", stripped)
                        if fm:
                            current_module["filePath"] = fm.group(1)
                    elif re.match(r"^\s*-\s+.+", stripped) and not stripped.startswith("- **"):
                        current_module["responsibilities"].append(
                            re.sub(r"^[\s-]+", "", stripped)
                        )

        # Save last module
        if current_module and current_module.get("id") and current_module.get("name"):
            data["modules"].append({
                "id": current_module["id"],
                "name": current_module["name"],
                "description": current_module.get("description", ""),
                "responsibilities": current_module.get("responsibilities", []),
                "dependencies": current_module.get("dependencies", []),
                "filePath": current_module.get("filePath", ""),
            })

        data["requirement"] = data.get("requirement", "").strip()

        if not data.get("requirement"):
            return None

        return OpenSpecDesign(**data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tasks Parser
# ---------------------------------------------------------------------------


def parse_tasks(markdown: str) -> OpenSpecTasks | None:
    """Parse a tasks markdown document."""
    try:
        tasks: list[dict] = []

        lines = markdown.split("\n")

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            # Top-level: ## ⬜ 1. Task Name
            if line.startswith("## "):
                content = stripped[3:]
                m = re.match(r"^[⬜🔄✅🚫]\s*(\d+)\.\s*(.+)$", content)
                if m:
                    task = {
                        "id": m.group(1),
                        "title": m.group(2),
                        "description": "",
                        "status": "pending",
                        "dependencies": [],
                    }
                    # Read description from next line
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if next_line and not next_line.startswith("#") and not next_line.startswith("-"):
                            task["description"] = next_line
                    tasks.append(task)

            # Sub-task: - ⬜ **1.1** Task Name
            if stripped.startswith("- ") and "**" in stripped:
                content = stripped[2:]
                m = re.match(r"^[⬜🔄✅🚫]\s*\*\*(\d+\.\d+)\*\*\s*(.+)$", content)
                if m:
                    tasks.append({
                        "id": m.group(1),
                        "title": m.group(2),
                        "description": "",
                        "status": "pending",
                        "dependencies": [],
                    })

        if not tasks:
            return None

        return OpenSpecTasks(tasks=[Task(**t) for t in tasks])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Spec Parser
# ---------------------------------------------------------------------------


def parse_spec(markdown: str) -> OpenSpecSpec | None:
    """Parse a spec markdown document."""
    try:
        scenarios: list[dict] = []

        lines = markdown.split("\n")
        current: dict | None = None
        in_gherkin = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Scenario header
            if stripped.lower().startswith("## scenario:"):
                if current and current.get("id") and current.get("name"):
                    scenarios.append(current)
                current = {"name": stripped[12:].strip()}
                continue

            # Scenario ID
            if stripped.startswith("**ID:**"):
                m = re.search(r"`(.+?)`", stripped)
                if m and current:
                    current["id"] = m.group(1)
                continue

            # Gherkin block
            if stripped.startswith("```gherkin"):
                in_gherkin = True
                continue
            if stripped.startswith("```") and in_gherkin:
                in_gherkin = False
                continue
            if in_gherkin and current:
                if stripped.startswith("GIVEN "):
                    current["given"] = stripped[6:]
                elif stripped.startswith("WHEN "):
                    current["when"] = stripped[5:]
                elif stripped.startswith("THEN "):
                    current["then"] = stripped[5:]

        # Save last
        if current and current.get("id") and current.get("name"):
            scenarios.append(current)

        if not scenarios:
            return None

        return OpenSpecSpec(scenarios=[Scenario(**s) for s in scenarios])
    except Exception:
        return None
