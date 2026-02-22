"""DesignInterfaces Tool - Define interfaces between modules."""

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


class ParameterSchema(Action):
    """Schema for a function parameter."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    name: str = Field(description="Parameter name")
    type: str = Field(description="Parameter type (e.g., 'string', 'int', 'List[User]')")
    optional: bool = Field(description="Whether this parameter is optional")
    description: str | None = Field(
        default=None,
        description="Parameter description"
    )


class InterfaceSchema(Action):
    """Schema for an interface definition."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str = Field(description="Unique interface identifier")
    name: str = Field(description="Interface name (e.g., 'UserService', 'createUser')")
    type: Literal["api", "class", "function", "interface", "type"] = Field(
        description="Type of interface"
    )
    signature: str = Field(
        description="Full type signature (e.g., '(name: str, email: str) -> User')"
    )
    description: str = Field(description="What this interface does")
    module_id: str = Field(
        description="ID of the module this interface belongs to"
    )
    parameters: list[ParameterSchema] | None = Field(
        default=None,
        description="List of parameters (for functions/methods)"
    )
    return_type: str | None = Field(
        default=None,
        description="Return type"
    )


class DataFieldSchema(Action):
    """Schema for a data model field."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    name: str = Field(description="Field name")
    type: str = Field(description="Field type")
    required: bool = Field(description="Whether this field is required")
    description: str | None = Field(
        default=None,
        description="Field description"
    )


class DataModelSchema(Action):
    """Schema for a data model."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str = Field(description="Unique model identifier")
    name: str = Field(description="Model name (e.g., 'User', 'Product')")
    fields: list[DataFieldSchema] = Field(description="List of fields")
    description: str | None = Field(
        default=None,
        description="Model description"
    )


class DesignInterfacesAction(Action):
    """Schema for designing interfaces."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    interfaces: list[InterfaceSchema] = Field(
        default_factory=list,
        description="List of interface definitions"
    )
    data_models: list[DataModelSchema] = Field(
        default_factory=list,
        description="List of data model definitions"
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("DesignInterfaces: ", style="bold blue")
        content.append(f"interfaces={len(self.interfaces)} models={len(self.data_models)}")
        return content


class DesignInterfacesObservation(Observation):
    """Observation from designing interfaces."""

    status: str = Field(description="Status: success or error")
    message: str = Field(description="Status message")
    interface_count: int = Field(description="Number of interfaces defined")
    model_count: int = Field(description="Number of data models defined")

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Interfaces: ", style="bold green")
        content.append(f"{self.interface_count} interfaces, {self.model_count} models")
        return content


TOOL_DESCRIPTION = """Define interfaces between modules.

This tool specifies:
- Function/method signatures
- Class interfaces
- API endpoints
- Data models

Interfaces should be designed based on the module structure from design_modules.
"""


class DesignInterfacesExecutor(ToolExecutor[DesignInterfacesAction, DesignInterfacesObservation]):
    """Executor for designing interfaces."""

    def __call__(
        self,
        action: DesignInterfacesAction,
        conversation: "LocalConversation | None" = None,
    ) -> DesignInterfacesObservation:
        """Store interfaces in conversation state."""
        if conversation:
            ctx = conversation.state.agent_state.get("toyshop_context", {})
            if "design" not in ctx:
                ctx["design"] = {}
            ctx["design"]["interfaces"] = [i.model_dump() for i in action.interfaces]
            ctx["design"]["data_models"] = [m.model_dump() for m in action.data_models]
            conversation.state.agent_state["toyshop_context"] = ctx

        return DesignInterfacesObservation(
            status="success",
            message=f"Defined {len(action.interfaces)} interfaces and {len(action.data_models)} data models",
            interface_count=len(action.interfaces),
            model_count=len(action.data_models),
        )


class DesignInterfacesTool(ToolDefinition[DesignInterfacesAction, DesignInterfacesObservation]):
    """Tool for designing interfaces."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence["DesignInterfacesTool"]:
        if params:
            raise ValueError("DesignInterfacesTool doesn't accept parameters")

        return [
            cls(
                action_type=DesignInterfacesAction,
                observation_type=DesignInterfacesObservation,
                description=TOOL_DESCRIPTION,
                executor=DesignInterfacesExecutor(),
                annotations=ToolAnnotations(
                    title="design_interfaces",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(DesignInterfacesTool.name, DesignInterfacesTool)
