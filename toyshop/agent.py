"""ToyShop Agent - Development workflow agent using openhands-sdk.

This module provides the ToyShopAgent that orchestrates the development workflow
using openhands-sdk's Agent infrastructure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.agent import AgentBase
from openhands.sdk.conversation import Conversation

from toyshop.tools import (
    AnalyzeInputTool,
    GenerateProposalTool,
    DesignModulesTool,
    DesignInterfacesTool,
    GenerateTasksTool,
    GenerateSpecTool,
)


# System prompt for ToyShop agent
TOYSHOP_SYSTEM_PROMPT = """You are ToyShop, a software development assistant that helps users plan and design software projects.

Your role is to guide users through the development planning process:

1. **Understand Requirements**: Use `analyze_input` to understand what the user wants to build.
2. **Create Proposal**: Use `generate_proposal` to create a structured project proposal.
3. **Design Architecture**: Use `design_modules` to define the system architecture.
4. **Define Interfaces**: Use `design_interfaces` to specify interfaces and data models.
5. **Break Down Tasks**: Use `generate_tasks` to create implementation tasks.
6. **Create Test Scenarios**: Use `generate_spec` to define test scenarios.

Work through these steps systematically. Ask clarifying questions if requirements are unclear.
Always provide structured, detailed outputs using the available tools.

After completing all steps, summarize the project plan for the user.
"""


def create_toyshop_llm(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLM:
    """Create an LLM instance for ToyShop.

    Defaults to openhands config.toml values if not specified.
    """
    from toyshop.llm import create_llm
    return create_llm(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,  # Lower temperature for more structured outputs
        timeout=180,
    )


def create_toyshop_agent(
    llm: LLM | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Agent:
    """Create a ToyShop agent with all workflow tools.

    Args:
        llm: Pre-configured LLM instance (optional)
        model: Model name (optional, uses config default)
        api_key: API key (optional, uses config default)
        base_url: Base URL (optional, uses config default)

    Returns:
        Configured Agent instance ready for development workflows
    """
    if llm is None:
        llm = create_toyshop_llm(model=model, api_key=api_key, base_url=base_url)

    # Create agent with ToyShop tools
    agent = Agent(
        llm=llm,
        tools=[
            {"name": AnalyzeInputTool.name},
            {"name": GenerateProposalTool.name},
            {"name": DesignModulesTool.name},
            {"name": DesignInterfacesTool.name},
            {"name": GenerateTasksTool.name},
            {"name": GenerateSpecTool.name},
        ],
        include_default_tools=["FinishTool"],  # Allow agent to signal completion
        system_prompt_kwargs={
            "custom_prompt": TOYSHOP_SYSTEM_PROMPT,
        },
    )

    return agent


class ToyShopConversation:
    """Manages a ToyShop development workflow conversation.

    This class wraps openhands Conversation to provide a simpler interface
    for the ToyShop workflow.
    """

    def __init__(
        self,
        workspace: str | Path,
        llm: LLM | None = None,
        agent: Agent | None = None,
    ):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

        if agent is None:
            agent = create_toyshop_agent(llm=llm)

        self.agent = agent
        self._conversation: Conversation | None = None
        self._context: dict[str, Any] = {}
        self._project_id: str | None = None
        self._snapshot_id: str | None = None

    def start(self) -> None:
        """Start the conversation."""
        if self._conversation is None:
            self._conversation = Conversation(
                agent=self.agent,
                workspace=str(self.workspace),
            )
            # Store toyshop context in agent_state (allowed by ConversationState)
            self._conversation.state.agent_state["toyshop_context"] = self._context

    def send_message(self, message: str) -> None:
        """Send a user message to the agent."""
        if self._conversation is None:
            self.start()

        self._conversation.send_message(message)

    def run(self) -> None:
        """Run the agent until completion."""
        if self._conversation is None:
            raise RuntimeError("Conversation not started. Call start() or send_message() first.")

        self._conversation.run()

    def get_context(self) -> dict[str, Any]:
        """Get the ToyShop context (proposal, design, tasks, etc.)."""
        if self._conversation:
            return self._conversation.state.agent_state.get("toyshop_context", {})
        return self._context

    def get_proposal(self) -> dict[str, Any] | None:
        """Get the generated proposal."""
        return self.get_context().get("proposal")

    def get_design(self) -> dict[str, Any] | None:
        """Get the generated design."""
        return self.get_context().get("design")

    def get_tasks(self) -> list[dict[str, Any]]:
        """Get the generated tasks."""
        return self.get_context().get("tasks", [])

    def get_spec(self) -> dict[str, Any] | None:
        """Get the generated specification."""
        return self.get_context().get("spec")

    # =========================================================================
    # Persistence Methods
    # =========================================================================

    def save_documents(self) -> dict[str, Path]:
        """Save all generated artifacts as Markdown documents.

        Returns:
            Dictionary mapping document type to file path
        """
        from toyshop.openspec_bridge import create_openspec_artifact

        saved = {}

        # Save proposal
        proposal = self.get_proposal()
        if proposal:
            path = create_openspec_artifact(self.workspace, "proposal", proposal)
            if path:
                saved["proposal"] = path

        # Save design
        design = self.get_design()
        if design:
            path = create_openspec_artifact(self.workspace, "design", design)
            if path:
                saved["design"] = path

        # Save tasks
        tasks = self.get_tasks()
        if tasks:
            path = create_openspec_artifact(self.workspace, "tasks", tasks)
            if path:
                saved["tasks"] = path

        # Save spec
        spec = self.get_spec()
        if spec:
            path = create_openspec_artifact(self.workspace, "spec", spec)
            if path:
                saved["spec"] = path

        return saved

    def persist_to_database(self, project_name: str) -> dict[str, str] | None:
        """Persist the architecture to SQLite database.

        Args:
            project_name: Name of the project

        Returns:
            Dictionary with project_id and snapshot_id, or None on failure
        """
        from toyshop.storage.database import (
            init_database,
            close_database,
            create_project,
            save_architecture_from_design,
        )

        design = self.get_design()
        if not design:
            return None

        try:
            db_path = self.workspace / ".toyshop" / "architecture.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)

            init_database(db_path)

            project = create_project(name=project_name, root_path=str(self.workspace))
            self._project_id = project["id"]

            # Extract modules and interfaces from design
            modules = design.get("modules", [])
            interfaces = design.get("interfaces", [])
            data_models = design.get("data_models", [])

            # Combine interfaces and data models for persistence
            all_interfaces = interfaces + [
                {"id": m.get("id"), "name": m.get("name"), "type": "data_model", **m}
                for m in data_models
            ]

            snapshot = save_architecture_from_design(
                project_id=self._project_id,
                modules=modules,
                interfaces=all_interfaces,
                source="agent_generated",
            )
            self._snapshot_id = snapshot["id"]

            close_database()

            return {
                "project_id": self._project_id,
                "snapshot_id": self._snapshot_id,
            }

        except Exception:
            return None

    def validate_with_openspec(self, strict: bool = False):
        """Validate generated documents using OpenSpec CLI.

        Args:
            strict: If True, warnings are treated as errors

        Returns:
            ValidationResult from OpenSpec bridge
        """
        from toyshop.openspec_bridge import OpenSpecBridge

        bridge = OpenSpecBridge(self.workspace)

        # First save documents
        self.save_documents()

        # Then validate
        return bridge.validate(strict=strict)

    @property
    def project_id(self) -> str | None:
        """Get the persisted project ID."""
        return self._project_id

    @property
    def snapshot_id(self) -> str | None:
        """Get the persisted snapshot ID."""
        return self._snapshot_id


def run_toyshop_workflow(
    user_input: str,
    project_name: str,
    workspace: str | Path,
    llm: LLM | None = None,
    persist: bool = True,
) -> ToyShopConversation:
    """Run a complete ToyShop workflow.

    This is the main entry point for running a development workflow.

    Args:
        user_input: The user's requirements description
        project_name: Name of the project
        workspace: Directory to store outputs
        llm: Optional pre-configured LLM instance
        persist: If True, save to database and documents

    Returns:
        ToyShopConversation with all generated artifacts
    """
    conversation = ToyShopConversation(
        workspace=workspace,
        llm=llm,
    )

    # Start with a clear instruction
    prompt = f"""Please help me design a software project.

Project Name: {project_name}

Requirements:
{user_input}

Please work through the following steps:
1. Analyze my requirements using analyze_input
2. Generate a proposal using generate_proposal
3. Design the architecture using design_modules
4. Define interfaces using design_interfaces
5. Break down into tasks using generate_tasks
6. Create test scenarios using generate_spec

After completing all steps, provide a summary of the project plan.
"""

    conversation.send_message(prompt)
    conversation.run()

    # Persist if requested
    if persist:
        conversation.save_documents()
        conversation.persist_to_database(project_name)

    return conversation
