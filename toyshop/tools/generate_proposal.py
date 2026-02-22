"""GenerateProposal Tool - Create OpenSpec proposal document."""

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


class CapabilitySchema(Action):
    """Schema for a capability."""

    name: str = Field(description="Capability name")
    description: str = Field(description="What this capability provides")
    priority: str = Field(description="Priority: must, should, could, wont")


class RiskSchema(Action):
    """Schema for a risk."""

    description: str = Field(description="Description of the risk")
    severity: str = Field(description="Severity: low, medium, high, critical")
    mitigation: str = Field(description="How to mitigate this risk")


class GenerateProposalAction(Action):
    """Schema for generating a proposal."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    project_name: str = Field(
        description="The name of the project"
    )
    background: str = Field(
        description="Background context and motivation"
    )
    problem: str = Field(
        description="The problem this project solves"
    )
    goals: list[str] = Field(
        description="List of goals to achieve"
    )
    non_goals: list[str] = Field(
        default_factory=list,
        description="List of explicit non-goals (out of scope)"
    )
    capabilities: list[CapabilitySchema] = Field(
        default_factory=list,
        description="List of capabilities the project will provide"
    )
    impacted_areas: list[str] = Field(
        default_factory=list,
        description="Areas of the codebase that will be affected"
    )
    risks: list[RiskSchema] = Field(
        default_factory=list,
        description="Potential risks and their mitigations"
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="External dependencies required"
    )
    timeline: str = Field(
        default="",
        description="Estimated timeline or phases"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("GenerateProposal: ", style="bold blue")
        content.append(f'project="{self.project_name}" ')
        content.append(f"goals={len(self.goals)}")
        return content


class GenerateProposalObservation(Observation):
    """Observation from generating a proposal."""

    proposal_id: str = Field(
        description="Unique identifier for the generated proposal"
    )
    status: str = Field(
        description="Status of proposal generation: success or error"
    )
    message: str = Field(
        description="Status message or error details"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Proposal: ", style="bold green")
        content.append(f"id={self.proposal_id} status={self.status}")
        return content


TOOL_DESCRIPTION = """Generate an OpenSpec proposal document.

This tool creates a structured proposal based on the analyzed requirements.
The proposal includes:
- Background and problem statement
- Goals and non-goals
- Capabilities with priorities
- Impacted areas
- Risks and mitigations
- Dependencies
- Timeline

The proposal serves as the foundation for the design phase.
"""


class GenerateProposalExecutor(ToolExecutor[GenerateProposalAction, GenerateProposalObservation]):
    """Executor for generating proposals."""

    def __call__(
        self,
        action: GenerateProposalAction,
        conversation: "LocalConversation | None" = None,
    ) -> GenerateProposalObservation:
        """Store the proposal in conversation state."""
        import uuid

        proposal_id = f"proposal-{uuid.uuid4().hex[:8]}"

        # Store in conversation state for workflow access
        if conversation:
            ctx = conversation.state.agent_state.get("toyshop_context", {})
            ctx["proposal"] = action.model_dump()
            ctx["proposal_id"] = proposal_id
            conversation.state.agent_state["toyshop_context"] = ctx

        return GenerateProposalObservation(
            proposal_id=proposal_id,
            status="success",
            message=f"Proposal generated for {action.project_name}",
        )


class GenerateProposalTool(ToolDefinition[GenerateProposalAction, GenerateProposalObservation]):
    """Tool for generating OpenSpec proposals."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence["GenerateProposalTool"]:
        if params:
            raise ValueError("GenerateProposalTool doesn't accept parameters")

        return [
            cls(
                action_type=GenerateProposalAction,
                observation_type=GenerateProposalObservation,
                description=TOOL_DESCRIPTION,
                executor=GenerateProposalExecutor(),
                annotations=ToolAnnotations(
                    title="generate_proposal",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(GenerateProposalTool.name, GenerateProposalTool)
