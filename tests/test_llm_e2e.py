"""End-to-end tests with real GLM-5 LLM.

These tests require a valid API key in openhands config.toml.
Run with: pytest tests/test_llm_e2e.py -v --timeout=300

WARNING: These tests make real API calls and may take 30-90 seconds each.
"""

import pytest
import tempfile
import shutil
import os
from pathlib import Path

from toyshop import create_llm, run_development_pipeline
from toyshop.llm import probe_llm
from toyshop.workflows import run_requirement_workflow, run_architecture_workflow


# Mark all tests in this file as e2e and slow
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


@pytest.fixture
def llm():
    """Create LLM instance from openhands config. Skip if LLM service unavailable."""
    if os.getenv("TOYSHOP_RUN_LIVE_E2E", "0") != "1":
        pytest.skip("Set TOYSHOP_RUN_LIVE_E2E=1 to run live E2E tests")

    _llm = create_llm()
    ok, err = probe_llm(_llm, timeout=15)
    if not ok:
        pytest.skip(f"LLM service unavailable: {err}")
    return _llm


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="toyshop_e2e_")
    yield workspace
    # Cleanup
    shutil.rmtree(workspace, ignore_errors=True)


class TestRealLLM:
    """Tests using real GLM-5 LLM."""

    @pytest.mark.timeout(120)
    def test_llm_connection(self, llm):
        """Test that LLM can connect and respond."""
        from toyshop.llm import chat_with_tool

        result = chat_with_tool(
            llm=llm,
            system_prompt="You are a helpful assistant.",
            user_content="Say 'OK' if you can hear me.",
            tool_name="respond",
            tool_description="Respond to the user",
            tool_parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )

        assert result is not None
        assert "message" in result
        assert len(result["message"]) > 0

    @pytest.mark.timeout(180)
    def test_requirement_workflow_real(self, llm):
        """Test requirement workflow with real LLM."""
        state = run_requirement_workflow(
            llm=llm,
            user_input="我需要一个简单的待办事项应用，用户可以添加、删除、标记完成待办项",
            project_name="TodoApp",
        )

        assert state.current_step == "done"
        assert state.error is None
        assert state.proposal is not None
        assert state.proposal.project_name == "TodoApp"
        assert len(state.proposal.goals) > 0
        assert len(state.proposal.capabilities) > 0
        assert "待办" in state.proposal.background or "todo" in state.proposal.background.lower()

    @pytest.mark.timeout(300)
    def test_architecture_workflow_real(self, llm):
        """Test architecture workflow with real LLM."""
        from toyshop.openspec.types import OpenSpecProposal, Capability, Priority

        # Create a simple proposal for testing
        proposal = OpenSpecProposal(
            projectName="SimpleAPI",
            background="需要一个简单的 REST API 服务",
            problem="没有现成的 API 服务",
            goals=["构建 API 服务", "支持基本 CRUD"],
            capabilities=[
                Capability(name="REST API", description="RESTful API endpoints", priority=Priority.MUST),
            ],
        )

        state = run_architecture_workflow(llm=llm, proposal=proposal)

        assert state.current_step == "done"
        assert state.error is None
        assert state.design is not None
        assert len(state.design.modules) > 0
        assert state.tasks is not None
        assert len(state.tasks.tasks) > 0

    @pytest.mark.timeout(600)
    def test_full_pipeline_real(self, llm, temp_workspace):
        """Test complete development pipeline with real LLM."""
        state = run_development_pipeline(
            user_input="创建一个简单的计算器程序，支持加减乘除四则运算",
            project_name="Calculator",
            workspace_dir=temp_workspace,
            llm=llm,
        )

        assert state.current_stage == "done"
        assert state.error is None
        assert state.requirement is not None
        assert state.requirement.proposal is not None
        assert state.architecture is not None
        assert state.architecture.design is not None

        # Check files were created
        ws = Path(temp_workspace)
        assert (ws / "openspec" / "proposal.md").exists()
        assert (ws / "openspec" / "design.md").exists()
        assert (ws / "openspec" / "tasks.md").exists()
        assert (ws / ".toyshop" / "architecture.db").exists()

        # Check database has project
        assert state.project_id is not None
        assert state.snapshot_id is not None
