"""DesignModules Tool - Design system architecture modules."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)

if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class ModuleSchema(Action):
    """Schema for a module definition."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str = Field(description="Unique module identifier (e.g., 'auth', 'api')")
    name: str = Field(description="Human-readable module name")
    description: str = Field(description="What this module does")
    responsibilities: list[str] = Field(description="List of module responsibilities")
    dependencies: list[str] = Field(description="List of module IDs this depends on")
    file_path: str = Field(
        description="Suggested file path for this module"
    )


class ArchitectureDecisionSchema(Action):
    """Schema for an architecture decision."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str = Field(description="Decision identifier (e.g., 'ADR-001')")
    title: str = Field(description="Decision title")
    context: str = Field(description="The context and problem being addressed")
    decision: str = Field(description="The decision made")
    consequences: str = Field(description="Consequences of this decision")
    alternatives: list[str] = Field(
        default_factory=list,
        description="Alternative options considered"
    )


class DesignModulesAction(Action):
    """Schema for designing modules."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    requirement: str = Field(
        description="The requirement or proposal this design addresses"
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Technical or business constraints"
    )
    decisions: list[ArchitectureDecisionSchema] = Field(
        default_factory=list,
        description="Architecture decisions made"
    )
    modules: list[ModuleSchema] = Field(
        default_factory=list,
        description="List of modules in the system"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("DesignModules: ", style="bold blue")
        content.append(f"modules={len(self.modules)} decisions={len(self.decisions)}")
        return content


class DesignModulesObservation(Observation):
    """Observation from designing modules."""

    design_id: str = Field(
        description="Unique identifier for the design"
    )
    status: str = Field(
        description="Status: success or error"
    )
    message: str = Field(
        description="Status message"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Design: ", style="bold green")
        content.append(f"id={self.design_id}")
        return content


TOOL_DESCRIPTION = """Design the system architecture modules.

This tool defines the high-level architecture:
- Architecture decisions (ADRs)
- Module structure and responsibilities
- Module dependencies
- File organization

The design should be based on the proposal and follow best practices.
"""


class DesignModulesExecutor(ToolExecutor[DesignModulesAction, DesignModulesObservation]):
    """Executor for designing modules."""

    def __call__(
        self,
        action: DesignModulesAction,
        conversation: "LocalConversation | None" = None,
    ) -> DesignModulesObservation:
        """Store the design in conversation state."""
        import uuid

        design_id = f"design-{uuid.uuid4().hex[:8]}"

        if conversation:
            ctx = conversation.state.agent_state.get("toyshop_context", {})
            ctx["design"] = action.model_dump()
            ctx["design_id"] = design_id
            conversation.state.agent_state["toyshop_context"] = ctx

        return DesignModulesObservation(
            design_id=design_id,
            status="success",
            message=f"Design created with {len(action.modules)} modules",
        )


class DesignModulesTool(ToolDefinition[DesignModulesAction, DesignModulesObservation]):
    """Tool for designing system modules."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence["DesignModulesTool"]:
        if params:
            raise ValueError("DesignModulesTool doesn't accept parameters")

        return [
            cls(
                action_type=DesignModulesAction,
                observation_type=DesignModulesObservation,
                description=TOOL_DESCRIPTION,
                executor=DesignModulesExecutor(),
                annotations=ToolAnnotations(
                    title="design_modules",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(DesignModulesTool.name, DesignModulesTool)
