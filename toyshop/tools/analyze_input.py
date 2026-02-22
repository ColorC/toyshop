"""AnalyzeInput Tool - Parse user requirements into structured analysis."""

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


class AnalyzeInputAction(Action):
    """Schema for analyzing user input."""

    user_input: str = Field(
        description="The raw user input describing what they want to build"
    )
    project_name: str = Field(
        description="The name of the project"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("AnalyzeInput: ", style="bold blue")
        content.append(f'project="{self.project_name}" ')
        content.append(f'input="{self.user_input[:50]}..."')
        return content


class AnalyzeInputObservation(Observation):
    """Observation from analyzing user input."""

    summary: str = Field(
        description="Brief summary of what the user wants"
    )
    domain: str = Field(
        description="The domain/category of the project (e.g., web app, CLI tool, API)"
    )
    key_features: list[str] = Field(
        default_factory=list,
        description="List of key features extracted from input"
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="List of constraints or requirements"
    )
    questions: list[str] = Field(
        default_factory=list,
        description="Clarifying questions if input is ambiguous"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Analysis: ", style="bold green")
        content.append(self.summary)
        return content


TOOL_DESCRIPTION = """Analyze user input to extract structured requirements.

This tool parses the user's natural language input and extracts:
- A summary of what they want to build
- The domain/category of the project
- Key features mentioned
- Constraints or requirements
- Clarifying questions if needed

Use this tool first when receiving user input to understand their needs.
"""


class AnalyzeInputExecutor(ToolExecutor[AnalyzeInputAction, AnalyzeInputObservation]):
    """Executor for analyzing user input.

    Note: This is a placeholder executor. The actual analysis is done by the LLM
    through tool-calling. This executor stores the result in the conversation state.
    """

    def __call__(
        self,
        action: AnalyzeInputAction,
        conversation: "LocalConversation | None" = None,
    ) -> AnalyzeInputObservation:
        """Store the analysis in conversation state for later use."""
        # The LLM will call this tool with the analysis results
        # We store it for the workflow to access
        if conversation:
            ctx = conversation.state.agent_state.get("toyshop_context", {})
            ctx["analysis"] = {
                "summary": action.user_input,  # Will be overwritten by LLM's actual analysis
                "project_name": action.project_name,
            }
            conversation.state.agent_state["toyshop_context"] = ctx

        # Return a placeholder - actual analysis comes from LLM
        return AnalyzeInputObservation(
            summary="Analysis stored",
            domain="unknown",
            key_features=[],
            constraints=[],
            questions=[],
        )


class AnalyzeInputTool(ToolDefinition[AnalyzeInputAction, AnalyzeInputObservation]):
    """Tool for analyzing user requirements."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence["AnalyzeInputTool"]:
        if params:
            raise ValueError("AnalyzeInputTool doesn't accept parameters")

        return [
            cls(
                action_type=AnalyzeInputAction,
                observation_type=AnalyzeInputObservation,
                description=TOOL_DESCRIPTION,
                executor=AnalyzeInputExecutor(),
                annotations=ToolAnnotations(
                    title="analyze_input",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(AnalyzeInputTool.name, AnalyzeInputTool)
