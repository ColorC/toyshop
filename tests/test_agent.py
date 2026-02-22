"""Tests for ToyShop Agent-based workflow.

These tests verify the new Agent API that uses openhands-sdk's
Agent infrastructure instead of direct LLM calls.
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from toyshop import (
    create_toyshop_agent,
    create_toyshop_llm,
    ToyShopConversation,
)
from toyshop.tools import (
    AnalyzeInputTool,
    GenerateProposalTool,
    DesignModulesTool,
    DesignInterfacesTool,
    GenerateTasksTool,
    GenerateSpecTool,
)


class TestTools:
    """Test tool definitions."""

    def test_analyze_input_tool_creation(self):
        """Test AnalyzeInputTool can be created."""
        tools = AnalyzeInputTool.create()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "analyze_input"  # Auto-converted from class name
        assert tool.action_type is not None
        assert tool.observation_type is not None

    def test_generate_proposal_tool_creation(self):
        """Test GenerateProposalTool can be created."""
        tools = GenerateProposalTool.create()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "generate_proposal"  # Auto-converted from class name

    def test_all_tools_registered(self):
        """Test all tools can be created."""
        expected_names = [
            "analyze_input",
            "generate_proposal",
            "design_modules",
            "design_interfaces",
            "generate_tasks",
            "generate_spec",
        ]
        tool_classes = [
            AnalyzeInputTool,
            GenerateProposalTool,
            DesignModulesTool,
            DesignInterfacesTool,
            GenerateTasksTool,
            GenerateSpecTool,
        ]
        for tool_class, expected_name in zip(tool_classes, expected_names):
            tools = tool_class.create()
            assert len(tools) == 1
            assert tools[0].name == expected_name


class TestAgentCreation:
    """Test agent creation."""

    def test_create_toyshop_llm(self):
        """Test LLM creation."""
        llm = create_toyshop_llm()
        assert llm is not None
        assert llm.model is not None

    def test_create_toyshop_agent(self):
        """Test agent creation."""
        agent = create_toyshop_agent()
        assert agent is not None
        assert agent.llm is not None

    def test_agent_has_tools(self):
        """Test agent has all ToyShop tools."""
        agent = create_toyshop_agent()
        # Tools are initialized when conversation starts
        assert agent.tools is not None
        assert len(agent.tools) > 0


class TestToyShopConversation:
    """Test ToyShopConversation."""

    def test_conversation_creation(self):
        """Test conversation can be created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conv = ToyShopConversation(workspace=tmpdir)
            assert conv.workspace == Path(tmpdir)
            assert conv.agent is not None

    def test_conversation_start(self):
        """Test conversation can be started."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conv = ToyShopConversation(workspace=tmpdir)
            conv.start()
            assert conv._conversation is not None

    def test_conversation_context(self):
        """Test conversation context management."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conv = ToyShopConversation(workspace=tmpdir)
            conv.start()

            # Context should be a dict (may have toyshop_context key)
            ctx = conv.get_context()
            assert isinstance(ctx, dict)


class TestOpenSpecBridge:
    """Test OpenSpec bridge."""

    def test_openspec_bridge_creation(self):
        """Test OpenSpecBridge can be created."""
        from toyshop.openspec_bridge import OpenSpecBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = OpenSpecBridge(tmpdir)
            assert bridge.workspace == Path(tmpdir)

    def test_create_openspec_artifact(self):
        """Test creating OpenSpec artifact files."""
        from toyshop.openspec_bridge import create_openspec_artifact

        with tempfile.TemporaryDirectory() as tmpdir:
            content = {
                "projectName": "TestProject",
                "background": "Test background",
                "problem": "Test problem",
                "goals": ["Goal 1", "Goal 2"],
            }

            path = create_openspec_artifact(tmpdir, "proposal", content)
            assert path is not None
            assert path.exists()
            assert path.name == "proposal.md"

            # Check content
            text = path.read_text()
            assert "TestProject" in text
            assert "Test background" in text

    def test_design_artifact(self):
        """Test creating design artifact."""
        from toyshop.openspec_bridge import create_openspec_artifact

        with tempfile.TemporaryDirectory() as tmpdir:
            content = {
                "requirement": "Test requirement",
                "modules": [
                    {
                        "id": "core",
                        "name": "Core Module",
                        "description": "Main module",
                        "responsibilities": ["Handle requests"],
                        "dependencies": [],
                        "filePath": "src/core/",
                    }
                ],
            }

            path = create_openspec_artifact(tmpdir, "design", content)
            assert path is not None
            text = path.read_text()
            assert "Core Module" in text

    def test_tasks_artifact(self):
        """Test creating tasks artifact."""
        from toyshop.openspec_bridge import create_openspec_artifact

        with tempfile.TemporaryDirectory() as tmpdir:
            content = [
                {
                    "id": "1",
                    "title": "Setup project",
                    "description": "Initialize project structure",
                    "status": "pending",
                    "dependencies": [],
                },
                {
                    "id": "1.1",
                    "title": "Create directory",
                    "description": "Create src directory",
                    "status": "pending",
                    "dependencies": [],
                },
            ]

            path = create_openspec_artifact(tmpdir, "tasks", content)
            assert path is not None
            text = path.read_text()
            assert "Setup project" in text
            assert "Create directory" in text
