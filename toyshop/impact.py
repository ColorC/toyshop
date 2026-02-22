"""Impact analysis and architecture guard for the change pipeline.

Phase 2 of the change pipeline:
- LLM analyzes change request against code snapshot + design.md
- Produces structured ImpactAnalysis (which modules/interfaces/scenarios are affected)
- Architecture guard checks design health (pure Python, no LLM)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from toyshop.llm import LLM
    from toyshop.snapshot import CodeSnapshot


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class ModuleImpact:
    module_id: str
    module_name: str
    change_type: str        # "modify" | "deprecate"
    reason: str


@dataclass
class InterfaceImpact:
    interface_id: str
    interface_name: str
    change_type: str        # "modify" | "add" | "deprecate"
    old_signature: str | None = None
    new_signature: str | None = None
    reason: str = ""


@dataclass
class ScenarioImpact:
    scenario_id: str
    change_type: str        # "modify" | "add" | "deprecate"
    reason: str = ""


@dataclass
class NewModuleSpec:
    name: str
    file_path: str
    description: str
    responsibilities: list[str] = field(default_factory=list)


@dataclass
class ImpactAnalysis:
    change_summary: str
    affected_modules: list[ModuleImpact] = field(default_factory=list)
    affected_interfaces: list[InterfaceImpact] = field(default_factory=list)
    affected_scenarios: list[ScenarioImpact] = field(default_factory=list)
    new_modules: list[NewModuleSpec] = field(default_factory=list)
    architecture_warnings: list[str] = field(default_factory=list)


# =============================================================================
# Serialization
# =============================================================================

def save_impact(impact: ImpactAnalysis, path: Path) -> None:
    path.write_text(json.dumps(asdict(impact), ensure_ascii=False, indent=2), encoding="utf-8")


def load_impact(path: Path) -> ImpactAnalysis:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ImpactAnalysis(
        change_summary=data.get("change_summary", ""),
        affected_modules=[ModuleImpact(**m) for m in data.get("affected_modules", [])],
        affected_interfaces=[InterfaceImpact(**i) for i in data.get("affected_interfaces", [])],
        affected_scenarios=[ScenarioImpact(**s) for s in data.get("affected_scenarios", [])],
        new_modules=[NewModuleSpec(**n) for n in data.get("new_modules", [])],
        architecture_warnings=data.get("architecture_warnings", []),
    )


# =============================================================================
# Architecture Guard (pure Python, no LLM)
# =============================================================================

def check_architecture_health(design) -> list[str]:
    """Check architecture health from an OpenSpecDesign object.

    Returns list of warning strings. Does not block — warnings are advisory.
    """
    warnings = []

    modules = design.modules if hasattr(design, "modules") else []
    interfaces = design.interfaces if hasattr(design, "interfaces") else []

    # 1. Responsibility bloat: module with > 5 responsibilities
    for mod in modules:
        resps = mod.responsibilities if hasattr(mod, "responsibilities") else []
        if len(resps) > 5:
            name = mod.name if hasattr(mod, "name") else str(mod)
            warnings.append(f"模块 {name} 职责过多 ({len(resps)})，考虑拆分")

    # 2. Circular dependency detection
    module_ids = {m.id for m in modules}
    dep_graph: dict[str, list[str]] = {}
    for mod in modules:
        deps = mod.dependencies if hasattr(mod, "dependencies") else []
        dep_graph[mod.id] = [d for d in deps if d in module_ids]

    cycles = _detect_cycles(dep_graph)
    for cycle in cycles:
        warnings.append(f"循环依赖: {' → '.join(cycle)}")

    # 3. Orphan modules: no deps and not depended on
    all_deps: set[str] = set()
    for deps in dep_graph.values():
        all_deps.update(deps)
    if len(modules) > 1:
        for mod in modules:
            if mod.id not in all_deps and not dep_graph.get(mod.id):
                warnings.append(f"模块 {mod.name} 是孤立的，无依赖关系")

    # 4. Interface-module consistency
    for intf in interfaces:
        mid = intf.module_id if hasattr(intf, "module_id") else ""
        if mid and mid not in module_ids:
            warnings.append(f"接口 {intf.name} 引用了不存在的模块 {mid}")

    # 5. Modules without interfaces
    modules_with_interfaces = set()
    for intf in interfaces:
        mid = intf.module_id if hasattr(intf, "module_id") else ""
        if mid:
            modules_with_interfaces.add(mid)
    for mod in modules:
        if mod.id not in modules_with_interfaces:
            warnings.append(f"模块 {mod.name} 没有定义任何接口")

    return warnings


def _detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Detect cycles in a directed graph using DFS."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    cycles: list[list[str]] = []
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                # Found cycle: extract from neighbor to current
                idx = path.index(neighbor)
                cycles.append(path[idx:] + [neighbor])
            elif color[neighbor] == WHITE:
                dfs(neighbor)
        path.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            dfs(node)

    return cycles


# =============================================================================
# LLM Impact Analysis
# =============================================================================

IMPACT_ANALYSIS_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "change_summary": {
            "type": "string",
            "description": "一句话描述变更内容",
        },
        "affected_modules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "module_id": {"type": "string", "description": "design.md 中的模块 ID"},
                    "module_name": {"type": "string"},
                    "change_type": {"type": "string", "enum": ["modify", "deprecate"]},
                    "reason": {"type": "string"},
                },
                "required": ["module_id", "module_name", "change_type", "reason"],
            },
        },
        "affected_interfaces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "interface_id": {"type": "string", "description": "design.md 中的接口 ID"},
                    "interface_name": {"type": "string"},
                    "change_type": {"type": "string", "enum": ["modify", "add", "deprecate"]},
                    "old_signature": {"type": "string", "description": "修改前签名（modify/deprecate 时填写）"},
                    "new_signature": {"type": "string", "description": "修改后签名（modify/add 时填写）"},
                    "reason": {"type": "string"},
                },
                "required": ["interface_id", "interface_name", "change_type", "reason"],
            },
        },
        "affected_scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scenario_id": {"type": "string", "description": "spec.md 中的场景 ID"},
                    "change_type": {"type": "string", "enum": ["modify", "add", "deprecate"]},
                    "reason": {"type": "string"},
                },
                "required": ["scenario_id", "change_type", "reason"],
            },
        },
        "new_modules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file_path": {"type": "string"},
                    "description": {"type": "string"},
                    "responsibilities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "file_path", "description"],
            },
        },
    },
    "required": ["change_summary", "affected_modules", "affected_interfaces"],
}


def run_impact_analysis(
    change_request: str,
    snapshot: "CodeSnapshot",
    design_md: str,
    spec_md: str,
    llm: "LLM",
) -> ImpactAnalysis:
    """Run LLM-based impact analysis.

    Args:
        change_request: Natural language description of the change
        snapshot: Code snapshot from snapshot.py
        design_md: Current design.md content
        spec_md: Current spec.md content
        llm: LLM instance

    Returns:
        ImpactAnalysis with affected modules/interfaces/scenarios
    """
    from toyshop.llm import chat_with_tool
    from toyshop.snapshot import CodeSnapshot

    # Build snapshot summary for LLM context
    snap_lines = []
    for m in snapshot.modules:
        snap_lines.append(f"## {m.file_path} ({m.line_count} lines)")
        for c in m.classes:
            snap_lines.append(f"  class {c.name}({', '.join(c.bases)})")
            for method in c.methods:
                snap_lines.append(f"    {method}")
        for f in m.functions:
            snap_lines.append(f"  {f.signature}")
    snapshot_text = "\n".join(snap_lines)

    system = """你是一位软件架构师。分析变更需求对现有代码库的影响。

你需要精准定位受影响的模块、接口和测试场景：
- affected_modules: 需要修改或废弃的现有模块（引用 design.md 中的 module ID）
- affected_interfaces: 需要修改、新增或废弃的接口（引用 design.md 中的 interface ID）
- affected_scenarios: 需要修改、新增或废弃的测试场景（引用 spec.md 中的 scenario ID）
- new_modules: 需要新建的模块

原则：
- 只标记真正受影响的部分，不要过度标记
- 每个 impact 必须有明确的 reason
- 新增接口的 change_type 为 "add"，interface_id 用新的唯一 ID
- 修改接口时提供 old_signature 和 new_signature

请使用 analyze_impact 工具返回分析结果。"""

    user_content = f"""## 变更需求

{change_request}

## 当前代码结构 (snapshot)

{snapshot_text}

## 当前架构设计 (design.md)

{design_md[:6000]}

## 当前测试场景 (spec.md)

{spec_md[:3000]}"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=user_content,
        tool_name="analyze_impact",
        tool_description="分析变更对现有代码库的影响",
        tool_parameters=IMPACT_ANALYSIS_TOOL_SCHEMA,
    )

    if not result:
        return ImpactAnalysis(change_summary="LLM 未返回分析结果")

    impact = ImpactAnalysis(
        change_summary=result.get("change_summary", ""),
        affected_modules=[ModuleImpact(**m) for m in result.get("affected_modules", [])],
        affected_interfaces=[InterfaceImpact(**i) for i in result.get("affected_interfaces", [])],
        affected_scenarios=[ScenarioImpact(**s) for s in result.get("affected_scenarios", [])],
        new_modules=[NewModuleSpec(**n) for n in result.get("new_modules", [])],
    )

    return impact
