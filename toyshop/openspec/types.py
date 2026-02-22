"""OpenSpec document types.

Pydantic models mirroring the TypeScript core/types.ts definitions.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Priority(str, Enum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InterfaceType(str, Enum):
    API = "api"
    CLASS = "class"
    FUNCTION = "function"
    INTERFACE = "interface"
    TYPE = "type"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Supporting Types
# ---------------------------------------------------------------------------


class Capability(BaseModel):
    name: str
    description: str
    priority: Priority


class Risk(BaseModel):
    description: str
    severity: Severity
    mitigation: str


class Goal(BaseModel):
    id: str
    description: str
    metrics: list[str] | None = None


class ArchitectureDecision(BaseModel):
    id: str
    title: str
    context: str
    decision: str
    consequences: str
    alternatives: list[str] | None = None


class ModuleDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str
    responsibilities: list[str]
    dependencies: list[str]
    file_path: str = Field(alias="filePath")


class Parameter(BaseModel):
    name: str
    type: str
    optional: bool
    description: str | None = None


class InterfaceDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    type: InterfaceType
    signature: str
    description: str
    module_id: str = Field(alias="moduleId")
    parameters: list[Parameter] | None = None
    return_type: str | None = Field(default=None, alias="returnType")


class DataField(BaseModel):
    name: str
    type: str
    required: bool
    description: str | None = None


class DataModel(BaseModel):
    id: str
    name: str
    fields: list[DataField]
    description: str | None = None


class ApiEndpoint(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    path: str
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"]
    description: str
    request_type: str | None = Field(default=None, alias="requestType")
    response_type: str | None = Field(default=None, alias="responseType")


class Tradeoff(BaseModel):
    aspect: str
    choice: str
    alternative: str
    rationale: str


class Task(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str  # X.Y format, e.g., "1.2"
    title: str
    description: str
    status: TaskStatus
    dependencies: list[str]
    estimated_complexity: Complexity | None = Field(default=None, alias="estimatedComplexity")
    assigned_module: str | None = Field(default=None, alias="assignedModule")


class Scenario(BaseModel):
    id: str
    name: str
    given: str
    when: str
    then: str


# ---------------------------------------------------------------------------
# OpenSpec Documents
# ---------------------------------------------------------------------------


class OpenSpecProposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: str = "OpenSpec v1.0 Proposal"
    project_name: str = Field(alias="projectName")

    # Why
    background: str
    problem: str
    goals: list[str]
    non_goals: list[str] = Field(default_factory=list, alias="nonGoals")

    # What Changes
    capabilities: list[Capability] = Field(default_factory=list)
    impacted_areas: list[str] = Field(default_factory=list, alias="impactedAreas")

    # Impact
    risks: list[Risk] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    timeline: str = ""


class OpenSpecDesign(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: str = "OpenSpec v1.0 Design"

    # Context
    requirement: str
    constraints: list[str] = Field(default_factory=list)

    # Goals
    goals: list[Goal] = Field(default_factory=list)

    # Decisions
    decisions: list[ArchitectureDecision] = Field(default_factory=list)

    # Architecture
    modules: list[ModuleDefinition] = Field(default_factory=list)
    interfaces: list[InterfaceDefinition] = Field(default_factory=list)
    data_models: list[DataModel] = Field(default_factory=list, alias="dataModels")
    api_endpoints: list[ApiEndpoint] = Field(default_factory=list, alias="apiEndpoints")

    # Risks
    risks: list[Risk] = Field(default_factory=list)
    tradeoffs: list[Tradeoff] = Field(default_factory=list)


class OpenSpecTasks(BaseModel):
    format: str = "OpenSpec v1.0 Tasks"
    tasks: list[Task] = Field(default_factory=list)


class OpenSpecSpec(BaseModel):
    format: str = "OpenSpec v1.0 Spec"
    scenarios: list[Scenario] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


class OpenSpecBundle(BaseModel):
    proposal: OpenSpecProposal
    design: OpenSpecDesign
    tasks: OpenSpecTasks
    spec: OpenSpecSpec


# ---------------------------------------------------------------------------
# Generator Inputs (for LLM tool schema)
# ---------------------------------------------------------------------------


class ProposalInput(BaseModel):
    """Input for generate_proposal()."""

    model_config = ConfigDict(populate_by_name=True)

    project_name: str = Field(alias="projectName")
    background: str
    problem: str
    goals: list[str]
    non_goals: list[str] = Field(default_factory=list, alias="nonGoals")
    capabilities: list[Capability] = Field(default_factory=list)
    impacted_areas: list[str] = Field(default_factory=list, alias="impactedAreas")
    risks: list[Risk] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    timeline: str = ""


class DesignInput(BaseModel):
    """Input for generate_design()."""

    model_config = ConfigDict(populate_by_name=True)

    requirement: str
    constraints: list[str] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    decisions: list[ArchitectureDecision] = Field(default_factory=list)
    modules: list[ModuleDefinition] = Field(default_factory=list)
    interfaces: list[InterfaceDefinition] = Field(default_factory=list)
    data_models: list[DataModel] = Field(default_factory=list, alias="dataModels")
    api_endpoints: list[ApiEndpoint] = Field(default_factory=list, alias="apiEndpoints")
    risks: list[Risk] = Field(default_factory=list)
    tradeoffs: list[Tradeoff] = Field(default_factory=list)


class TasksInput(BaseModel):
    """Input for generate_tasks()."""

    tasks: list[Task]


class SpecInput(BaseModel):
    """Input for generate_spec()."""

    scenarios: list[Scenario]
