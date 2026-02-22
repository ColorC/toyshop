"""Tests for workflow nodes with mocked LLM."""

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from toyshop.llm import LLM
from toyshop.workflows.requirement import (
    run_requirement_workflow,
    RequirementState,
    CollectedInfo,
    Clarification,
)
from toyshop.workflows.architecture import (
    run_architecture_workflow,
    ArchitectureState,
)
from toyshop.openspec.types import (
    OpenSpecProposal,
    OpenSpecDesign,
    Priority,
    Severity,
    Capability,
    Risk,
    ModuleDefinition,
    InterfaceDefinition,
    InterfaceType,
)


# ---------------------------------------------------------------------------
# Mock Helpers
# ---------------------------------------------------------------------------

@dataclass
class MockFunction:
    """Mock function object with real string attributes."""
    name: str
    arguments: str


@dataclass
class MockToolCall:
    """Mock tool call object."""
    id: str
    type: str
    function: MockFunction


@dataclass
class MockMessage:
    """Mock message object."""
    role: str
    content: str
    tool_calls: list


@dataclass
class MockChoice:
    """Mock choice object."""
    message: MockMessage


@dataclass
class MockResponse:
    """Mock response object."""
    choices: list


def make_mock_response_with_tool(tool_name: str, arguments: str):
    """Create a mock litellm response with a tool call."""
    return MockResponse(
        choices=[
            MockChoice(
                message=MockMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        MockToolCall(
                            id="call_123",
                            type="function",
                            function=MockFunction(name=tool_name, arguments=arguments),
                        )
                    ],
                )
            )
        ]
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm():
    """Create a mock LLM instance."""
    llm = MagicMock(spec=LLM)
    llm.model = "openai/glm-5"
    llm.base_url = "https://open.bigmodel.cn/api/coding/paas/v4"
    llm.temperature = 0.3
    llm.timeout = 180
    llm.api_key = MagicMock()
    llm.api_key.get_secret_value.return_value = "test-key"
    return llm


# ---------------------------------------------------------------------------
# Requirement Workflow Tests
# ---------------------------------------------------------------------------

class TestRequirementWorkflow:
    def test_analyze_input(self, mock_llm):
        """Test the analyze_input node."""
        from toyshop.workflows.requirement import analyze_input

        response = make_mock_response_with_tool(
            "analyze_input",
            '{"domain": "e-commerce", "target_users": ["buyers"], "core_features": ["cart"], "constraints": [], "existing_context": []}'
        )

        with patch("toyshop.llm.litellm.completion", return_value=response):
            state = RequirementState(
                user_input="I want to build an online store",
                project_name="MyStore",
            )

            result = analyze_input(mock_llm, state)

            assert result.get("collected_info") is not None
            assert result["collected_info"].domain == "e-commerce"
            assert "cart" in result["collected_info"].core_features
            assert result["current_step"] == "questions"

    def test_generate_proposal_node(self, mock_llm):
        """Test the generate_proposal_node."""
        from toyshop.workflows.requirement import generate_proposal_node

        response = make_mock_response_with_tool(
            "generate_proposal_data",
            '''{
                "project_name": "TestApp",
                "background": "Need an app",
                "problem": "No app exists",
                "goals": ["Build app"],
                "non_goals": [],
                "capabilities": [{"name": "Core", "description": "Core feature", "priority": "must"}],
                "impacted_areas": ["backend"],
                "risks": [],
                "dependencies": [],
                "timeline": "2 weeks"
            }'''
        )

        with patch("toyshop.llm.litellm.completion", return_value=response):
            state = RequirementState(
                user_input="Build an app",
                project_name="TestApp",
                collected_info=CollectedInfo(
                    domain="mobile",
                    target_users=["users"],
                    core_features=["feature1"],
                ),
            )

            result = generate_proposal_node(mock_llm, state)

            assert result.get("proposal") is not None
            assert result["proposal"].project_name == "TestApp"
            assert "Build app" in result["proposal"].goals
            assert result["current_step"] == "done"


# ---------------------------------------------------------------------------
# Architecture Workflow Tests
# ---------------------------------------------------------------------------

class TestArchitectureWorkflow:
    @pytest.fixture
    def sample_proposal(self):
        """Create a sample proposal for testing."""
        return OpenSpecProposal(
            projectName="TestApp",
            background="Need a test application",
            problem="No testing tool available",
            goals=["Build test tool", "Support multiple formats"],
            capabilities=[
                Capability(name="Core", description="Core functionality", priority=Priority.MUST)
            ],
            risks=[
                Risk(description="API changes", severity=Severity.MEDIUM, mitigation="Versioning")
            ],
        )

    def test_analyze_proposal_node(self, mock_llm, sample_proposal):
        """Test the analyze_proposal_node."""
        from toyshop.workflows.architecture import analyze_proposal_node

        response = make_mock_response_with_tool(
            "analyze_proposal",
            '{"goals": ["Scalability", "Maintainability"], "decisions": ["Use microservices"], "tradeoffs": ["Complexity vs flexibility"]}'
        )

        with patch("toyshop.llm.litellm.completion", return_value=response):
            state = ArchitectureState(proposal=sample_proposal)
            result = analyze_proposal_node(mock_llm, state)

            assert result.get("analysis") is not None
            assert "Scalability" in result["analysis"].goals
            assert result["current_step"] == "modules"

    def test_design_modules_node(self, mock_llm, sample_proposal):
        """Test the design_modules_node."""
        from toyshop.workflows.architecture import design_modules_node, ArchitectureAnalysis

        response = make_mock_response_with_tool(
            "design_modules",
            '''{
                "modules": [{
                    "id": "core",
                    "name": "Core",
                    "description": "Core module",
                    "responsibilities": ["Handle logic"],
                    "dependencies": [],
                    "filePath": "src/core/index.ts"
                }],
                "dataModels": []
            }'''
        )

        with patch("toyshop.llm.litellm.completion", return_value=response):
            state = ArchitectureState(
                proposal=sample_proposal,
                analysis=ArchitectureAnalysis(goals=["Test"], decisions=["Use modules"], tradeoffs=[]),
            )
            result = design_modules_node(mock_llm, state)

            assert result.get("design") is not None
            assert len(result["design"].modules) == 1
            assert result["design"].modules[0].id == "core"
            assert result["current_step"] == "interfaces"


# ---------------------------------------------------------------------------
# Integration Tests (Full Workflow)
# ---------------------------------------------------------------------------

class TestWorkflowIntegration:
    def test_requirement_workflow_full(self, mock_llm):
        """Test full requirement workflow with mocked LLM responses."""
        call_count = [0]

        responses = [
            # analyze_input response
            make_mock_response_with_tool(
                "analyze_input",
                '{"domain": "web", "target_users": ["developers"], "core_features": ["api"], "constraints": [], "existing_context": []}'
            ),
            # generate_questions response
            make_mock_response_with_tool(
                "generate_questions",
                '{"questions": [{"question": "What language?", "reason": "Need to know"}]}'
            ),
            # generate_proposal response
            make_mock_response_with_tool(
                "generate_proposal_data",
                '''{
                    "project_name": "API",
                    "background": "Need API",
                    "problem": "No API",
                    "goals": ["Build API"],
                    "capabilities": [{"name": "REST", "description": "REST API", "priority": "must"}],
                    "impacted_areas": [],
                    "risks": [],
                    "dependencies": [],
                    "timeline": "1 week"
                }'''
            ),
        ]

        def mock_response(*args, **kwargs):
            idx = min(call_count[0], len(responses) - 1)
            call_count[0] += 1
            return responses[idx]

        with patch("toyshop.llm.litellm.completion", side_effect=mock_response):
            state = run_requirement_workflow(
                llm=mock_llm,
                user_input="Build a REST API",
                project_name="MyAPI",
            )

            assert state.current_step == "done"
            assert state.proposal is not None
            assert state.proposal.project_name == "API"
