"""Architecture workflow nodes.

Each node takes an LLM and current state, calls the LLM with tool schemas,
and produces architecture design artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from toyshop.llm import LLM, chat_with_tool
from toyshop.openspec.types import (
    Goal,
    ArchitectureDecision,
    ModuleDefinition,
    InterfaceDefinition,
    DataModel,
    Task,
    Scenario,
    OpenSpecDesign,
    OpenSpecTasks,
    OpenSpecSpec,
    DesignInput,
    TasksInput,
    SpecInput,
    InterfaceType,
    TaskStatus,
)
from toyshop.openspec.generator import (
    generate_design,
    render_design_markdown,
    generate_tasks,
    render_tasks_markdown,
    generate_spec,
    render_spec_markdown,
)

if TYPE_CHECKING:
    from toyshop.openspec.types import OpenSpecProposal


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class ArchitectureAnalysis:
    """Analysis results from proposal."""

    goals: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)


@dataclass
class ArchitectureState:
    """State for the architecture workflow."""

    # Input
    proposal: "OpenSpecProposal | None" = None

    # Stage outputs
    analysis: ArchitectureAnalysis | None = None
    design: OpenSpecDesign | None = None
    design_markdown: str = ""
    tasks: OpenSpecTasks | None = None
    tasks_markdown: str = ""
    spec: OpenSpecSpec | None = None
    spec_markdown: str = ""

    # Control
    current_step: str = "analyze"
    error: str | None = None


# ---------------------------------------------------------------------------
# Tool Schemas
# ---------------------------------------------------------------------------

ANALYZE_PROPOSAL_TOOL = {
    "type": "function",
    "function": {
        "name": "analyze_proposal",
        "description": "Analyze a proposal to extract architectural implications",
        "parameters": {
            "type": "object",
            "properties": {
                "goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Architecture goals derived from proposal",
                },
                "decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key architectural decisions to make",
                },
                "tradeoffs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tradeoffs to consider",
                },
            },
            "required": ["goals", "decisions"],
        },
    },
}

DESIGN_MODULES_TOOL = {
    "type": "function",
    "function": {
        "name": "design_modules",
        "description": "Design software modules based on requirements",
        "parameters": {
            "type": "object",
            "properties": {
                "modules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Short identifier (e.g., 'api')"},
                            "name": {"type": "string", "description": "Display name"},
                            "description": {"type": "string"},
                            "responsibilities": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "IDs of modules this depends on",
                            },
                            "filePath": {"type": "string", "description": "Suggested file path"},
                        },
                        "required": ["id", "name", "description", "responsibilities", "filePath"],
                    },
                },
                "dataModels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "fields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "type": {"type": "string"},
                                        "required": {"type": "boolean"},
                                    },
                                    "required": ["name", "type", "required"],
                                },
                            },
                        },
                        "required": ["id", "name", "fields"],
                    },
                },
            },
            "required": ["modules"],
        },
    },
}

DESIGN_INTERFACES_TOOL = {
    "type": "function",
    "function": {
        "name": "design_interfaces",
        "description": "Design interfaces for the modules",
        "parameters": {
            "type": "object",
            "properties": {
                "interfaces": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": ["api", "class", "function", "type"]},
                            "signature": {"type": "string", "description": "Function/type signature"},
                            "description": {"type": "string"},
                            "moduleId": {"type": "string", "description": "Parent module ID"},
                        },
                        "required": ["id", "name", "type", "signature", "description", "moduleId"],
                    },
                },
            },
            "required": ["interfaces"],
        },
    },
}

GENERATE_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_task_list",
        "description": "Generate hierarchical task list for implementation",
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Task ID in X.Y format (e.g., '1.2')"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "IDs of tasks this depends on",
                            },
                            "assignedModule": {"type": "string", "description": "Module ID to implement this"},
                        },
                        "required": ["id", "title", "description"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
}

GENERATE_SPEC_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_specification",
        "description": "Generate Gherkin-style specification scenarios",
        "parameters": {
            "type": "object",
            "properties": {
                "scenarios": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "given": {"type": "string", "description": "Precondition"},
                            "when": {"type": "string", "description": "Action"},
                            "then": {"type": "string", "description": "Expected outcome"},
                        },
                        "required": ["id", "name", "given", "when", "then"],
                    },
                },
            },
            "required": ["scenarios"],
        },
    },
}


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def analyze_proposal_node(llm: LLM, state: ArchitectureState) -> dict[str, Any]:
    """Analyze the proposal for architectural implications."""
    if not state.proposal:
        return {"error": "No proposal to analyze", "current_step": "analyze"}

    system = """你是一位资深软件架构师。分析项目提案，提取：
- 架构目标
- 关键决策点
- 需要权衡的因素

请使用 analyze_proposal 工具返回分析结果。"""

    context = f"""项目: {state.proposal.project_name}

背景: {state.proposal.background}

问题: {state.proposal.problem}

目标: {', '.join(state.proposal.goals)}

能力: {', '.join(c.name for c in state.proposal.capabilities)}

约束: {', '.join(state.proposal.dependencies)}
"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=context,
        tool_name="analyze_proposal",
        tool_description="Analyze proposal for architectural implications",
        tool_parameters=ANALYZE_PROPOSAL_TOOL["function"]["parameters"],
    )

    if result:
        return {
            "analysis": ArchitectureAnalysis(
                goals=result.get("goals", []),
                decisions=result.get("decisions", []),
                tradeoffs=result.get("tradeoffs", []),
            ),
            "current_step": "modules",
        }
    return {"error": "Failed to analyze proposal", "current_step": "analyze"}


def design_modules_node(llm: LLM, state: ArchitectureState) -> dict[str, Any]:
    """Design the module structure."""
    if not state.proposal or not state.analysis:
        return {"error": "Missing proposal or analysis", "current_step": "analyze"}

    system = """你是一位软件架构师。根据需求分析设计 Python 模块结构：
- 每个模块应有单一职责
- 模块间依赖应最小化
- 考虑可测试性和可维护性
- 文件路径使用 Python 包格式（如 mdtable/parser.py）

请使用 design_modules 工具返回模块设计。"""

    context = f"""项目: {state.proposal.project_name}

架构目标: {', '.join(state.analysis.goals)}

关键决策: {', '.join(state.analysis.decisions)}

核心能力: {', '.join(c.name + ': ' + c.description for c in state.proposal.capabilities)}
"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=context,
        tool_name="design_modules",
        tool_description="Design software modules",
        tool_parameters=DESIGN_MODULES_TOOL["function"]["parameters"],
    )

    if result:
        modules = [
            ModuleDefinition(
                id=m.get("id", ""),
                name=m.get("name", ""),
                description=m.get("description", ""),
                responsibilities=m.get("responsibilities", []),
                dependencies=m.get("dependencies", []),
                filePath=m.get("filePath", ""),
            )
            for m in result.get("modules", [])
        ]

        data_models = [
            DataModel(
                id=dm.get("id", ""),
                name=dm.get("name", ""),
                fields=[
                    {"name": f.get("name"), "type": f.get("type"), "required": f.get("required", True)}
                    for f in dm.get("fields", [])
                ],
            )
            for dm in result.get("dataModels", [])
        ]

        # Store partial design for later nodes
        if state.design:
            design = state.design.model_copy(update={"modules": modules, "data_models": data_models})
        else:
            design = OpenSpecDesign(
                requirement=state.proposal.problem,
                modules=modules,
                data_models=data_models,
            )

        return {"design": design, "current_step": "interfaces"}

    return {"error": "Failed to design modules", "current_step": "modules"}


def design_interfaces_node(llm: LLM, state: ArchitectureState) -> dict[str, Any]:
    """Design interfaces for modules."""
    if not state.design or not state.design.modules:
        return {"error": "No modules to design interfaces for", "current_step": "modules"}

    system = """你是一位软件架构师。为已设计的模块定义接口：
- 每个接口应有清晰的 Python 签名（def/class 语法）
- 接口应体现模块的核心功能
- 考虑类型安全
- 签名必须是合法的 Python 代码，例如：
  - `def parse(text: str) -> Table`
  - `class QueryBuilder`
  - `def __init__(self, table: Table) -> None`
- 不要使用 TypeScript/JavaScript 语法

请使用 design_interfaces 工具返回接口设计。"""

    modules_desc = "\n".join(
        f"- {m.id}: {m.name} - {m.description}"
        for m in state.design.modules
    )

    context = f"""模块列表:
{modules_desc}
"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=context,
        tool_name="design_interfaces",
        tool_description="Design module interfaces",
        tool_parameters=DESIGN_INTERFACES_TOOL["function"]["parameters"],
    )

    if result:
        interfaces = [
            InterfaceDefinition(
                id=i.get("id", ""),
                name=i.get("name", ""),
                type=InterfaceType(i.get("type", "function")),
                signature=i.get("signature", ""),
                description=i.get("description", ""),
                module_id=i.get("moduleId", ""),
            )
            for i in result.get("interfaces", [])
        ]

        design = state.design.model_copy(update={"interfaces": interfaces})
        return {"design": design, "current_step": "tasks"}

    return {"error": "Failed to design interfaces", "current_step": "interfaces"}


def generate_tasks_node(llm: LLM, state: ArchitectureState) -> dict[str, Any]:
    """Generate implementation task list."""
    if not state.design:
        return {"error": "No design to generate tasks from", "current_step": "modules"}

    system = """你是一位项目经理。根据架构设计生成实现任务列表：
- 任务应按层级组织（1, 1.1, 1.2, 2, 2.1...）
- 每个任务应可独立完成
- 标注任务依赖关系
- 分配到具体模块

请使用 generate_task_list 工具返回任务列表。"""

    modules_desc = "\n".join(
        f"- {m.id}: {m.name}"
        for m in state.design.modules
    )

    context = f"""模块:
{modules_desc}

项目目标: {state.proposal.goals if state.proposal else 'N/A'}
"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=context,
        tool_name="generate_task_list",
        tool_description="Generate implementation tasks",
        tool_parameters=GENERATE_TASKS_TOOL["function"]["parameters"],
    )

    if result:
        tasks = [
            Task(
                id=t.get("id", ""),
                title=t.get("title", ""),
                description=t.get("description", ""),
                status=TaskStatus.PENDING,
                dependencies=t.get("dependencies", []),
                assigned_module=t.get("assignedModule"),
            )
            for t in result.get("tasks", [])
        ]

        tasks_doc = generate_tasks(TasksInput(tasks=tasks))
        md = render_tasks_markdown(tasks_doc)

        return {"tasks": tasks_doc, "tasks_markdown": md, "current_step": "spec"}

    return {"error": "Failed to generate tasks", "current_step": "tasks"}


def generate_spec_node(llm: LLM, state: ArchitectureState) -> dict[str, Any]:
    """Generate specification scenarios."""
    if not state.design:
        return {"error": "No design to generate spec from", "current_step": "modules"}

    system = """你是一位QA工程师。根据设计生成验收测试场景：
- 使用 GIVEN/WHEN/THEN 格式
- 覆盖主要功能路径
- 包含边界条件

请使用 generate_specification 工具返回场景列表。"""

    interfaces_desc = "\n".join(
        f"- {i.name}: {i.description}"
        for i in state.design.interfaces
    )

    context = f"""接口:
{interfaces_desc}

能力: {', '.join(c.name for c in state.proposal.capabilities) if state.proposal else 'N/A'}
"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=context,
        tool_name="generate_specification",
        tool_description="Generate specification scenarios",
        tool_parameters=GENERATE_SPEC_TOOL["function"]["parameters"],
    )

    if result:
        scenarios = [
            Scenario(
                id=s.get("id", ""),
                name=s.get("name", ""),
                given=s.get("given", ""),
                when=s.get("when", ""),
                then=s.get("then", ""),
            )
            for s in result.get("scenarios", [])
        ]

        spec_doc = generate_spec(SpecInput(scenarios=scenarios))
        md = render_spec_markdown(spec_doc)

        # Finalize design markdown
        design_md = render_design_markdown(state.design)

        return {
            "spec": spec_doc,
            "spec_markdown": md,
            "design_markdown": design_md,
            "current_step": "done",
        }

    return {"error": "Failed to generate spec", "current_step": "spec"}


# ---------------------------------------------------------------------------
# Workflow Runner
# ---------------------------------------------------------------------------


def run_architecture_workflow(
    llm: LLM,
    proposal: "OpenSpecProposal",
) -> ArchitectureState:
    """Run the complete architecture workflow."""
    state = ArchitectureState(proposal=proposal)

    # Step 1: Analyze proposal
    updates = analyze_proposal_node(llm, state)
    for k, v in updates.items():
        setattr(state, k, v)

    if state.error or state.current_step != "modules":
        return state

    # Step 2: Design modules
    updates = design_modules_node(llm, state)
    for k, v in updates.items():
        setattr(state, k, v)

    if state.error or state.current_step != "interfaces":
        return state

    # Step 3: Design interfaces
    updates = design_interfaces_node(llm, state)
    for k, v in updates.items():
        setattr(state, k, v)

    if state.error or state.current_step != "tasks":
        return state

    # Step 4: Generate tasks
    updates = generate_tasks_node(llm, state)
    for k, v in updates.items():
        setattr(state, k, v)

    if state.error or state.current_step != "spec":
        return state

    # Step 5: Generate spec
    updates = generate_spec_node(llm, state)
    for k, v in updates.items():
        setattr(state, k, v)

    return state
