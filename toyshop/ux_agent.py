"""UX Agent - Automated user experience testing agent.

Simulates a real user interacting with target workflows and generates
structured UX test reports.

Ported from:
- extensions/pipelines/src/testing/ux-agent.ts
- _personal_copilot/src/agents/loop_framework/agents/ux_agent_loop_adapter.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pydantic import Field
from rich.text import Text

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm.message import TextContent
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


class UxEvaluationMode(str, Enum):
    """Evaluation mode determines what the UX Agent focuses on."""
    E2E = "e2e"                      # Input vs output comparison only
    PROCESS_HEALTH = "process_health"  # Process execution quality
    FULL = "full"                    # Both e2e and process health


# System prompts for different evaluation modes
UX_AGENT_SYSTEM_PROMPT_E2E = """# UX Agent — 端到端输出质量评估

## 角色定义
你是一个严格的架构设计输出质量评估 Agent。你的职责是对比原始需求和最终输出文档，评估设计质量。

## 核心原则
1. **严格基于原始需求评价** — 只评价需求中明确提到的功能，绝不"脑补"需求中没有的功能
2. 不需要启动工作流或进行交互 — 你只需要读取文件并评估
3. 客观、公正，基于事实

## 评估流程

### Step 1: 理解任务 (1 轮)
- 仔细阅读任务描述中的原始需求
- 提取需求中明确列出的功能点作为 checklist
- 注意：只提取需求中**明确写出**的功能，不要推断隐含功能

### Step 2: 读取输出文件 (1-3 轮)
- 使用 file_read 读取 design.md、tasks.md、spec.md
- 如需要可读取 raw-state.json 查看结构化数据

### Step 3: 逐项对照评估 (1-2 轮)
对照 Step 1 的 checklist，逐项检查：
- 每个明确需求是否有对应的模块/接口/实体/API 设计
- 设计是否合理（架构层次、模块职责、接口方向）
- 输出格式是否完整（design.md 包含所有必需 section）

### Step 4: 填写报告并完成 (1 轮)
使用 report_builder 填写：
- requirementCompleteness: 逐项列出需求 checklist 的覆盖情况
- outputQuality: 设计文档质量评价（结构、一致性、完整性）
- assessmentLevel: 1-5
- assessmentReason: 基于事实的评估理由
然后调用 finish。

## 评估标准
- 1 = 完美：所有明确需求都有高质量设计覆盖
- 2 = 优秀：所有明确需求有覆盖，设计有小瑕疵
- 3 = 可接受：核心需求有覆盖，但设计有明显问题（层次违规、实体重复等）
- 4 = 不可接受：部分明确需求缺失设计覆盖
- 5 = 失败：大量需求未覆盖或输出格式严重不合规

## 重要约束
- **禁止评价需求中没有明确提到的功能缺失**
- 不要启动工作流（不使用 run_workflow / respond_input）
- 最后必须调用 finish
"""


UX_AGENT_SYSTEM_PROMPT_FULL = """# UX Agent — 自动化用户体验测试 Agent

## 角色定义
你是一个专业的用户体验测试 Agent。你的职责是模拟真实用户与被测系统进行交互，评估系统的易用性、功能完整性和输出质量，最终生成结构化的测试报告。

## 核心原则
1. 基于实际交互评估，不要编造测试结果
2. 模拟真实用户行为，使用自然语言回答问题
3. 系统性地覆盖被测系统的主要功能
4. 客观、公正地评价系统表现

## 测试三阶段

### Phase 1: 理解 (1-2 轮)
目标：充分理解被测系统和测试任务

操作步骤：
1. 仔细阅读任务描述，理解测试目标
2. 如果任务中提到了脚本文件，使用 file_read 读取源码
3. 分析被测系统的预期行为、交互模式和输入输出格式
4. 制定测试策略：准备要输入的测试数据

### Phase 2: 执行 (2-5 轮)
目标：与被测系统进行完整的交互测试

操作步骤：
1. 使用 run_workflow 启动被测工作流
2. 阅读工作流的输出和提问
3. 使用 respond_input 模拟用户回答
4. 持续交互直到工作流完成或出错

### Phase 3: 报告 (1-2 轮)
目标：生成完整的 UX 测试报告

操作步骤：
1. 使用 report_builder(action="set_field") 填写评估字段
2. 使用 report_builder(action="generate") 生成完整报告
3. 使用 finish 工具提交最终结果

## 评估标准
- 1 = 完美：所有功能正常，交互流畅，输出高质量
- 2 = 优秀：主要功能正常，小瑕疵不影响使用
- 3 = 可接受：核心功能可用，但有明显改进空间
- 4 = 不可接受：关键功能缺失或严重 bug
- 5 = 失败：无法完成基本交互或系统崩溃

## 重要约束
- 每个阶段不超过指定的轮次限制
- 必须按 Phase 1 → 2 → 3 的顺序执行
- 最后必须调用 finish 工具
"""


# ============================================================================
# UX Tools
# ============================================================================

class FileReadAction(Action):
    """Schema for file read action."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    path: str = Field(description="文件路径（相对或绝对）")
    max_lines: int = Field(default=200, description="最大读取行数")


class FileReadObservation(Observation):
    """Observation from file read."""

    file_content: str = Field(default="", description="文件内容")
    total_lines: int = Field(default=0, description="总行数")
    truncated: bool = Field(default=False, description="是否被截断")
    path: str = Field(default="", description="绝对路径")

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("FileRead: ", style="bold blue")
        content.append(f"{self.path} ({self.total_lines} lines)")
        return content


class FileReadExecutor(ToolExecutor[FileReadAction, FileReadObservation]):
    """Executor for file read."""

    def __call__(
        self,
        action: FileReadAction,
        conversation: "LocalConversation | None" = None,
    ) -> FileReadObservation:
        """Read file content."""
        try:
            # Get workspace from conversation state
            cwd = Path.cwd()
            if conversation and hasattr(conversation.state, "workspace"):
                workspace = conversation.state.workspace
                # Handle both dict and object workspace types
                if isinstance(workspace, dict):
                    cwd = Path(workspace.get("working_dir", cwd))
                elif hasattr(workspace, "working_dir"):
                    cwd = Path(workspace.working_dir)

            file_path = (cwd / action.path).resolve()

            # Check if it's a directory
            if file_path.is_dir():
                return FileReadObservation(
                    content=[TextContent(text=f"Error: {file_path} is a directory, not a file")],
                    file_content="",
                    total_lines=0,
                    truncated=False,
                    path=str(file_path),
                    is_error=True,
                )

            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            truncated = len(lines) > action.max_lines
            result = "\n".join(lines[:action.max_lines]) if truncated else content

            return FileReadObservation(
                content=[TextContent(text=result)],
                file_content=result,
                total_lines=len(lines),
                truncated=truncated,
                path=str(file_path),
            )
        except Exception as e:
            return FileReadObservation(
                content=[TextContent(text=f"Error: {str(e)}")],
                file_content="",
                total_lines=0,
                truncated=False,
                path=action.path,
                is_error=True,
            )


class FileReadTool(ToolDefinition[FileReadAction, FileReadObservation]):
    """Tool for reading files."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> list["FileReadTool"]:
        if params:
            raise ValueError("FileReadTool doesn't accept parameters")

        return [
            cls(
                action_type=FileReadAction,
                observation_type=FileReadObservation,
                description="读取文件内容。用于理解被测脚本的源码和行为。",
                executor=FileReadExecutor(),
                annotations=ToolAnnotations(
                    title="file_read",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


class ReportBuilderAction(Action):
    """Schema for report builder action."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    action_type_field: str = Field(
        alias="action",
        description="操作类型: set_field, get_fields, generate"
    )
    field_name: str | None = Field(
        default=None,
        alias="fieldName",
        description="字段名 (set_field 时使用)"
    )
    field_value: Any = Field(
        default=None,
        alias="fieldValue",
        description="字段值 (set_field 时使用)"
    )

    @property
    def action(self) -> str:
        return self.action_type_field


class ReportBuilderObservation(Observation):
    """Observation from report builder."""

    status: str = Field(default="", description="操作状态")
    message: str = Field(default="", description="状态消息")
    report: str | None = Field(default=None, description="生成的报告")

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("ReportBuilder: ", style="bold blue")
        content.append(self.status)
        return content


class ReportBuilderExecutor(ToolExecutor[ReportBuilderAction, ReportBuilderObservation]):
    """Executor for building UX test reports."""

    def __call__(
        self,
        action: ReportBuilderAction,
        conversation: "LocalConversation | None" = None,
    ) -> ReportBuilderObservation:
        """Build report incrementally."""
        if not conversation:
            return ReportBuilderObservation(
                content=[TextContent(text="Error: No conversation context")],
                status="error",
                message="No conversation context",
                is_error=True,
            )

        # Get or create report state
        ctx = conversation.state.agent_state.get("ux_report", {})

        if action.action == "set_field":
            if not action.field_name:
                return ReportBuilderObservation(
                    content=[TextContent(text="Error: fieldName required for set_field")],
                    status="error",
                    message="fieldName required for set_field",
                    is_error=True,
                )
            ctx[action.field_name] = action.field_value
            conversation.state.agent_state["ux_report"] = ctx
            return ReportBuilderObservation(
                content=[TextContent(text=f"Success: Field '{action.field_name}' set")],
                status="success",
                message=f"Field '{action.field_name}' set",
            )

        elif action.action == "get_fields":
            ctx_str = str(ctx)
            return ReportBuilderObservation(
                content=[TextContent(text=ctx_str)],
                status="success",
                message=ctx_str,
            )

        elif action.action == "generate":
            report = self._generate_report(ctx)
            conversation.state.agent_state["ux_report_final"] = report
            return ReportBuilderObservation(
                content=[TextContent(text=f"Report generated:\n\n{report}")],
                status="success",
                message="Report generated",
                report=report,
            )

        return ReportBuilderObservation(
            content=[TextContent(text=f"Error: Unknown action: {action.action}")],
            status="error",
            message=f"Unknown action: {action.action}",
            is_error=True,
        )

    def _generate_report(self, ctx: dict[str, Any]) -> str:
        """Generate markdown report from fields."""
        lines = ["# UX 测试报告", ""]

        if "testTarget" in ctx:
            lines.append(f"## 测试目标\n{ctx['testTarget']}\n")

        if "exitStatus" in ctx:
            lines.append(f"## 退出状态\n{ctx['exitStatus']}\n")

        lines.append("## 评估结果\n")

        if "requirementUnderstanding" in ctx:
            lines.append(f"- 需求理解: {'✅' if ctx['requirementUnderstanding'] else '❌'}")

        if "requirementCompleteness" in ctx:
            lines.append(f"- 功能完整性: {ctx['requirementCompleteness']}")

        if "outputQuality" in ctx:
            lines.append(f"- 输出质量: {ctx['outputQuality']}")

        if "interactionFluency" in ctx:
            lines.append(f"- 交互流畅度: {ctx['interactionFluency']}")

        lines.append("")

        if "assessmentLevel" in ctx:
            level = ctx["assessmentLevel"]
            lines.append(f"## 综合评分: {level}/5\n")

        if "assessmentReason" in ctx:
            lines.append(f"## 评估理由\n{ctx['assessmentReason']}\n")

        if "suggestions" in ctx and ctx["suggestions"]:
            lines.append("## 改进建议\n")
            for s in ctx["suggestions"]:
                lines.append(f"- {s}")
            lines.append("")

        return "\n".join(lines)


class ReportBuilderTool(ToolDefinition[ReportBuilderAction, ReportBuilderObservation]):
    """Tool for building UX test reports."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> list["ReportBuilderTool"]:
        if params:
            raise ValueError("ReportBuilderTool doesn't accept parameters")

        return [
            cls(
                action_type=ReportBuilderAction,
                observation_type=ReportBuilderObservation,
                description="""构建 UX 测试报告。

操作类型:
- set_field: 设置报告字段 (需要 fieldName 和 fieldValue)
- get_fields: 获取当前所有字段
- generate: 生成最终报告

可用字段:
- testTarget: 被测系统名称/路径
- exitStatus: 正常完成/异常退出/超时
- requirementUnderstanding: true/false
- requirementCompleteness: 功能完整性描述
- outputQuality: 输出质量评价
- interactionFluency: 交互流畅度
- assessmentLevel: 1-5 评估等级
- assessmentReason: 评估理由
- suggestions: 改进建议数组
""",
                executor=ReportBuilderExecutor(),
                annotations=ToolAnnotations(
                    title="report_builder",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


# Register tools
register_tool(FileReadTool.name, FileReadTool)
register_tool(ReportBuilderTool.name, ReportBuilderTool)


# ============================================================================
# UX Agent
# ============================================================================

@dataclass
class UXTestResult:
    """Result of UX test."""
    finished: bool
    assessment_level: int
    summary: str
    report: str
    iterations: int


def create_ux_agent(
    llm: LLM,
    mode: UxEvaluationMode = UxEvaluationMode.E2E,
) -> Agent:
    """Create a UX evaluation agent.

    Args:
        llm: LLM instance
        mode: Evaluation mode (e2e, process_health, or full)

    Returns:
        Configured Agent for UX testing
    """
    # Select system prompt based on mode
    if mode == UxEvaluationMode.E2E:
        system_prompt = UX_AGENT_SYSTEM_PROMPT_E2E
    else:
        system_prompt = UX_AGENT_SYSTEM_PROMPT_FULL

    agent = Agent(
        llm=llm,
        tools=[
            {"name": FileReadTool.name},
            {"name": ReportBuilderTool.name},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={
            "custom_prompt": system_prompt,
        },
    )

    return agent


def run_ux_evaluation(
    target_workspace: str | Path,
    task_description: str,
    llm: LLM | None = None,
    mode: UxEvaluationMode = UxEvaluationMode.E2E,
    max_iterations: int = 15,
) -> UXTestResult:
    """Run UX evaluation on a target workspace.

    Args:
        target_workspace: Directory containing the artifacts to evaluate
        task_description: Description of what to test
        llm: LLM instance (created from config if not provided)
        mode: Evaluation mode
        max_iterations: Maximum number of agent iterations

    Returns:
        UXTestResult with assessment and report
    """
    from toyshop import create_toyshop_llm

    if llm is None:
        llm = create_toyshop_llm()

    target_workspace = Path(target_workspace)

    agent = create_ux_agent(llm, mode)

    conversation = Conversation(
        agent=agent,
        workspace=str(target_workspace),
    )

    # Initialize UX report state
    conversation.state.agent_state["ux_report"] = {}

    # Send task
    prompt = f"""请评估以下工作空间的输出质量。

工作空间: {target_workspace}

任务描述:
{task_description}

请按照评估流程进行，最后使用 report_builder 生成报告并调用 finish 提交结果。
"""

    conversation.send_message(prompt)
    conversation.run()

    # Extract results
    ux_report = conversation.state.agent_state.get("ux_report", {})
    final_report = conversation.state.agent_state.get("ux_report_final", "")

    assessment_level = ux_report.get("assessmentLevel", 5)
    assessment_reason = ux_report.get("assessmentReason", "评估未完成")

    return UXTestResult(
        finished=bool(final_report),
        assessment_level=assessment_level,
        summary=assessment_reason,
        report=final_report or "报告生成失败",
        iterations=max_iterations,  # Placeholder
    )
