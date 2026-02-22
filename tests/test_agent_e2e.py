"""End-to-end tests for new Agent architecture with real GLM-5 LLM.

These tests use the new openhands-sdk Agent infrastructure.
Run with: pytest tests/test_agent_e2e.py -v --timeout=600

WARNING: These tests make real API calls and may take 60-180 seconds each.
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from toyshop import (
    create_toyshop_llm,
    create_toyshop_agent,
    ToyShopConversation,
    run_toyshop_workflow,
    create_ux_agent,
    run_ux_evaluation,
    UxEvaluationMode,
)


# Mark all tests in this file as e2e and slow
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


@pytest.fixture
def llm():
    """Create LLM instance from openhands config."""
    return create_toyshop_llm()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="toyshop_agent_e2e_")
    yield workspace
    # Cleanup
    shutil.rmtree(workspace, ignore_errors=True)


class TestAgentArchitecture:
    """Tests for new Agent architecture with real LLM."""

    @pytest.mark.timeout(120)
    def test_llm_connection(self, llm):
        """Test that LLM can connect using openhands-sdk."""
        from openhands.sdk.llm.message import Message, TextContent

        response = llm.completion(
            messages=[
                Message(
                    role="user",
                    content=[TextContent(text="Say 'OK' if you can hear me.")],
                )
            ]
        )

        assert response.response is not None
        assert response.response.choices
        msg = response.response.choices[0].message
        assert msg.content or getattr(msg, 'reasoning_content', None)

    @pytest.mark.timeout(180)
    def test_agent_creation(self, llm):
        """Test that ToyShop agent can be created with tools."""
        agent = create_toyshop_agent(llm=llm)

        assert agent is not None
        assert agent.llm == llm
        # Check tools are registered
        assert len(agent.tools) >= 6  # 6 ToyShop tools + FinishTool

    @pytest.mark.timeout(300)
    def test_simple_conversation(self, llm, temp_workspace):
        """Test a simple conversation with ToyShop agent."""
        agent = create_toyshop_agent(llm=llm)
        conversation = ToyShopConversation(
            workspace=temp_workspace,
            agent=agent,
        )

        conversation.send_message("请帮我分析这个需求：创建一个简单的计数器")
        conversation.run()

        # Check context was populated
        context = conversation.get_context()
        assert context is not None

    @pytest.mark.timeout(600)
    def test_full_workflow(self, llm, temp_workspace):
        """Test complete ToyShop workflow with real LLM."""
        conversation = run_toyshop_workflow(
            user_input="创建一个简单的计数器程序，支持增加、减少、重置功能",
            project_name="Counter",
            workspace=temp_workspace,
            llm=llm,
            persist=True,
        )

        # Check artifacts were generated
        context = conversation.get_context()
        assert context is not None

        # Check proposal
        proposal = conversation.get_proposal()
        assert proposal is not None
        assert "counter" in proposal.get("projectName", "").lower() or \
               "计数器" in proposal.get("background", "")

        # Check design
        design = conversation.get_design()
        assert design is not None
        assert len(design.get("modules", [])) > 0

        # Check tasks
        tasks = conversation.get_tasks()
        assert len(tasks) > 0

        # Check files were created
        ws = Path(temp_workspace)
        assert (ws / "openspec" / "proposal.md").exists(), "proposal.md should exist"
        assert (ws / "openspec" / "design.md").exists(), "design.md should exist"
        assert (ws / "openspec" / "tasks.md").exists(), "tasks.md should exist"

        # Check database persistence
        assert conversation.project_id is not None
        assert conversation.snapshot_id is not None
        assert (ws / ".toyshop" / "architecture.db").exists()

    @pytest.mark.timeout(300)
    def test_workflow_without_persist(self, llm, temp_workspace):
        """Test workflow without database persistence."""
        conversation = run_toyshop_workflow(
            user_input="创建一个简单的问候程序",
            project_name="Greeter",
            workspace=temp_workspace,
            llm=llm,
            persist=False,
        )

        # Check artifacts were generated in memory
        proposal = conversation.get_proposal()
        assert proposal is not None

        # Check database was NOT created
        assert conversation.project_id is None
        assert conversation.snapshot_id is None


class TestUXAgent:
    """Tests for UX Agent with real LLM."""

    @pytest.mark.timeout(300)
    def test_ux_agent_creation(self, llm):
        """Test that UX agent can be created."""
        agent = create_ux_agent(llm=llm, mode=UxEvaluationMode.E2E)

        assert agent is not None
        assert agent.llm == llm
        # Check tools are registered
        assert len(agent.tools) >= 2  # FileRead + ReportBuilder + FinishTool

    @pytest.mark.timeout(600)
    def test_ux_evaluation_e2e(self, llm, temp_workspace):
        """Test UX evaluation in E2E mode."""
        # First create a simple workspace with artifacts to evaluate
        openspec_dir = Path(temp_workspace) / "openspec"
        openspec_dir.mkdir(parents=True, exist_ok=True)

        # Create a simple proposal for evaluation
        proposal_content = """# Test Project

## Background
这是一个测试项目，用于验证 UX Agent 的评估能力。

## Goals
- 提供基本功能
- 保持简单易用

## Capabilities
- **核心功能** (must)
  用户可以进行基本操作
"""
        (openspec_dir / "proposal.md").write_text(proposal_content)

        # Run UX evaluation
        result = run_ux_evaluation(
            target_workspace=temp_workspace,
            task_description="评估这个简单项目的设计质量",
            llm=llm,
            mode=UxEvaluationMode.E2E,
            max_iterations=10,
        )

        # Check result
        assert result is not None
        assert result.finished is True
        assert 1 <= result.assessment_level <= 5
        assert result.report is not None
        assert len(result.report) > 0


class TestOpenSpecBridge:
    """Tests for OpenSpec CLI bridge."""

    def test_bridge_availability(self, temp_workspace):
        """Test if OpenSpec CLI is available."""
        from toyshop.openspec_bridge import OpenSpecBridge

        bridge = OpenSpecBridge(temp_workspace)

        # Just check if is_available works
        # (may be False if CLI not installed, which is OK for CI)
        assert isinstance(bridge.is_available(), bool)

    def test_validate_empty_workspace(self, temp_workspace):
        """Test validation of empty workspace."""
        from toyshop.openspec_bridge import OpenSpecBridge

        bridge = OpenSpecBridge(temp_workspace)

        if not bridge.is_available():
            pytest.skip("OpenSpec CLI not available")

        result = bridge.validate()

        assert result is not None
        assert isinstance(result.valid, bool)
        assert isinstance(result.errors, list)
        assert isinstance(result.warnings, list)
