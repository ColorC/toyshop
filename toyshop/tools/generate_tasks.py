"""GenerateTasks Tool - Break down design into implementation tasks."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

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


class TaskSchema(Action):
    """Schema for an implementation task."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str = Field(
        description="Task ID in X.Y format (e.g., '1.2', '2.1')"
    )
    title: str = Field(description="Task title")
    description: str = Field(description="Detailed task description")
    status: Literal["pending", "in_progress", "completed", "blocked"] = Field(
        default="pending",
        description="Task status"
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="List of task IDs this task depends on"
    )
    estimated_complexity: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description="Estimated complexity"
    )
    assigned_module: str | None = Field(
        default=None,
        description="Module ID this task is assigned to"
    )


class GenerateTasksAction(Action):
    """Schema for generating tasks."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    tasks: list[TaskSchema] = Field(
        default_factory=list,
        description="List of implementation tasks"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("GenerateTasks: ", style="bold blue")
        content.append(f"tasks={len(self.tasks)}")
        return content


class GenerateTasksObservation(Observation):
    """Observation from generating tasks."""

    status: str = Field(description="Status: success or error")
    message: str = Field(description="Status message")
    task_count: int = Field(description="Number of tasks generated")

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Tasks: ", style="bold green")
        content.append(f"{self.task_count} tasks generated")
        return content


TOOL_DESCRIPTION = """Break down the design into implementation tasks.

This tool creates a task list based on the design:
- Tasks are hierarchical (X.Y format)
- Tasks have dependencies
- Tasks are assigned to modules
- Tasks have complexity estimates

Tasks should be ordered to allow incremental implementation.
"""


class GenerateTasksExecutor(ToolExecutor[GenerateTasksAction, GenerateTasksObservation]):
    """Executor for generating tasks."""

    def __call__(
        self,
        action: GenerateTasksAction,
        conversation: "LocalConversation | None" = None,
    ) -> GenerateTasksObservation:
        """Store tasks in conversation state."""
        if conversation:
            ctx = conversation.state.agent_state.get("toyshop_context", {})
            ctx["tasks"] = [t.model_dump() for t in action.tasks]
            conversation.state.agent_state["toyshop_context"] = ctx

        return GenerateTasksObservation(
            status="success",
            message=f"Generated {len(action.tasks)} implementation tasks",
            task_count=len(action.tasks),
        )


class GenerateTasksTool(ToolDefinition[GenerateTasksAction, GenerateTasksObservation]):
    """Tool for generating implementation tasks."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence["GenerateTasksTool"]:
        if params:
            raise ValueError("GenerateTasksTool doesn't accept parameters")

        return [
            cls(
                action_type=GenerateTasksAction,
                observation_type=GenerateTasksObservation,
                description=TOOL_DESCRIPTION,
                executor=GenerateTasksExecutor(),
                annotations=ToolAnnotations(
                    title="generate_tasks",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(GenerateTasksTool.name, GenerateTasksTool)
