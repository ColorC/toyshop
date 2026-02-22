"""GenerateSpec Tool - Create test scenarios and specifications."""

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


class ScenarioSchema(Action):
    """Schema for a test scenario (Given-When-Then)."""

    id: str = Field(description="Scenario identifier")
    name: str = Field(description="Scenario name")
    given: str = Field(description="Initial state/preconditions")
    when: str = Field(description="Action or event")
    then: str = Field(description="Expected outcome")


class GenerateSpecAction(Action):
    """Schema for generating specifications."""

    scenarios: list[ScenarioSchema] = Field(
        default_factory=list,
        description="List of test scenarios"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("GenerateSpec: ", style="bold blue")
        content.append(f"scenarios={len(self.scenarios)}")
        return content


class GenerateSpecObservation(Observation):
    """Observation from generating specifications."""

    status: str = Field(description="Status: success or error")
    message: str = Field(description="Status message")
    scenario_count: int = Field(description="Number of scenarios generated")

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Spec: ", style="bold green")
        content.append(f"{self.scenario_count} scenarios")
        return content


TOOL_DESCRIPTION = """Create test scenarios and specifications.

This tool generates BDD-style test scenarios:
- Given-When-Then format
- Coverage of main use cases
- Edge cases and error scenarios

Scenarios should cover the capabilities defined in the proposal.
"""


class GenerateSpecExecutor(ToolExecutor[GenerateSpecAction, GenerateSpecObservation]):
    """Executor for generating specifications."""

    def __call__(
        self,
        action: GenerateSpecAction,
        conversation: "LocalConversation | None" = None,
    ) -> GenerateSpecObservation:
        """Store specifications in conversation state."""
        if conversation:
            ctx = conversation.state.agent_state.get("toyshop_context", {})
            ctx["spec"] = {
                "scenarios": [s.model_dump() for s in action.scenarios]
            }
            conversation.state.agent_state["toyshop_context"] = ctx

        return GenerateSpecObservation(
            status="success",
            message=f"Generated {len(action.scenarios)} test scenarios",
            scenario_count=len(action.scenarios),
        )


class GenerateSpecTool(ToolDefinition[GenerateSpecAction, GenerateSpecObservation]):
    """Tool for generating specifications."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence["GenerateSpecTool"]:
        if params:
            raise ValueError("GenerateSpecTool doesn't accept parameters")

        return [
            cls(
                action_type=GenerateSpecAction,
                observation_type=GenerateSpecObservation,
                description=TOOL_DESCRIPTION,
                executor=GenerateSpecExecutor(),
                annotations=ToolAnnotations(
                    title="generate_spec",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(GenerateSpecTool.name, GenerateSpecTool)
