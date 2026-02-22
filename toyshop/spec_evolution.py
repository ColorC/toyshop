"""Incremental OpenSpec document evolution for the change pipeline.

Phase 3 of the change pipeline:
- Takes current openspec docs + ImpactAnalysis
- Uses LLM to produce updated versions of each document
- Verifies that unchanged parts are preserved
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toyshop.llm import LLM
    from toyshop.impact import ImpactAnalysis


# =============================================================================
# Impact summary helpers
# =============================================================================

def _format_impact_for_prompt(impact: "ImpactAnalysis") -> str:
    """Format ImpactAnalysis as readable text for LLM prompts."""
    lines = [f"变更摘要: {impact.change_summary}", ""]

    if impact.affected_modules:
        lines.append("受影响的模块:")
        for m in impact.affected_modules:
            lines.append(f"  - [{m.change_type}] {m.module_name} ({m.module_id}): {m.reason}")
        lines.append("")

    if impact.new_modules:
        lines.append("新增模块:")
        for n in impact.new_modules:
            lines.append(f"  - {n.name} ({n.file_path}): {n.description}")
        lines.append("")

    if impact.affected_interfaces:
        lines.append("受影响的接口:")
        for i in impact.affected_interfaces:
            detail = ""
            if i.change_type == "modify" and i.old_signature and i.new_signature:
                detail = f" [{i.old_signature} → {i.new_signature}]"
            elif i.change_type == "add" and i.new_signature:
                detail = f" [{i.new_signature}]"
            lines.append(f"  - [{i.change_type}] {i.interface_name} ({i.interface_id}){detail}: {i.reason}")
        lines.append("")

    if impact.affected_scenarios:
        lines.append("受影响的测试场景:")
        for s in impact.affected_scenarios:
            lines.append(f"  - [{s.change_type}] {s.scenario_id}: {s.reason}")
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# Evolve functions
# =============================================================================

def evolve_proposal(
    current_proposal_md: str,
    change_request: str,
    impact: "ImpactAnalysis",
    llm: "LLM",
) -> str:
    """Update proposal.md to reflect the change request.

    Appends change context to the existing proposal while preserving
    all original content.
    """
    from toyshop.llm import chat_with_tool

    impact_text = _format_impact_for_prompt(impact)

    system = """你是一位需求分析师。更新现有的 proposal.md 以反映新的变更需求。

规则：
- 保留原有的所有内容不变
- 在 "## What Changes" 部分追加新的 capabilities
- 在 "## Impact" 部分追加新的风险评估
- 如果原文没有这些章节，在末尾追加 "## Change: <变更摘要>" 章节
- 输出完整的更新后文档，不是 diff

请使用 update_proposal 工具返回更新后的完整文档。"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=f"## 变更需求\n\n{change_request}\n\n## 影响分析\n\n{impact_text}\n\n## 当前 proposal.md\n\n{current_proposal_md}",
        tool_name="update_proposal",
        tool_description="返回更新后的完整 proposal.md 内容",
        tool_parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "更新后的完整 proposal.md 内容"},
            },
            "required": ["content"],
        },
    )

    if result and result.get("content"):
        return result["content"]
    return current_proposal_md


def evolve_design(
    current_design_md: str,
    impact: "ImpactAnalysis",
    llm: "LLM",
) -> str:
    """Update design.md to reflect the impact analysis.

    Modifies/adds/deprecates modules and interfaces as specified in impact.
    Preserves all unchanged modules and interfaces verbatim.
    """
    from toyshop.llm import chat_with_tool

    impact_text = _format_impact_for_prompt(impact)

    # Build explicit list of unchanged items for preservation
    unchanged_note = _build_unchanged_note(current_design_md, impact)

    system = """你是一位软件架构师。根据影响分析更新 design.md。

规则：
- 对标记为 "modify" 的模块/接口：更新其内容
- 对标记为 "add" 的接口：在对应模块下新增
- 对标记为 "deprecate" 的模块/接口：标记为 deprecated 但保留
- 对新增模块：在 Architecture 部分添加
- 所有未受影响的模块和接口必须原样保留，一字不改
- 签名必须是合法的 Python 代码
- 输出完整的更新后文档

请使用 update_design 工具返回更新后的完整文档。"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=f"## 影响分析\n\n{impact_text}\n\n## 未受影响的部分（必须原样保留）\n\n{unchanged_note}\n\n## 当前 design.md\n\n{current_design_md}",
        tool_name="update_design",
        tool_description="返回更新后的完整 design.md 内容",
        tool_parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "更新后的完整 design.md 内容"},
            },
            "required": ["content"],
        },
    )

    if result and result.get("content"):
        return result["content"]
    return current_design_md


def evolve_tasks(
    impact: "ImpactAnalysis",
    llm: "LLM",
) -> str:
    """Generate tasks.md for the change (only change-related tasks).

    Unlike greenfield tasks.md which covers the entire project,
    change tasks.md only contains tasks for the affected parts.
    """
    from toyshop.llm import chat_with_tool

    impact_text = _format_impact_for_prompt(impact)

    system = """你是一位项目经理。根据影响分析生成变更任务列表。

规则：
- 只生成与变更相关的任务
- 任务按依赖顺序排列
- 每个任务关联到受影响的模块
- 使用标准 tasks.md 格式：
  ## 1. 顶层任务
  ### 1.1 子任务
  **Dependencies:** ...
  **Module:** ...

请使用 generate_tasks 工具返回任务列表。"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=f"## 影响分析\n\n{impact_text}",
        tool_name="generate_tasks",
        tool_description="返回变更任务的 tasks.md 内容",
        tool_parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "tasks.md 内容"},
            },
            "required": ["content"],
        },
    )

    if result and result.get("content"):
        return result["content"]
    return "# Tasks\n\nNo tasks generated.\n"


def evolve_spec(
    current_spec_md: str,
    impact: "ImpactAnalysis",
    llm: "LLM",
) -> str:
    """Update spec.md to reflect the impact analysis.

    Adds new scenarios, modifies changed scenarios, preserves unchanged ones.
    """
    from toyshop.llm import chat_with_tool

    impact_text = _format_impact_for_prompt(impact)

    system = """你是一位测试架构师。根据影响分析更新 spec.md。

规则：
- 对标记为 "add" 的场景：新增 Given/When/Then 场景
- 对标记为 "modify" 的场景：更新其内容
- 对标记为 "deprecate" 的场景：标记为 deprecated 但保留
- 所有未受影响的场景必须原样保留
- 新场景的 ID 格式为 TC-XXX（递增）
- 输出完整的更新后文档

请使用 update_spec 工具返回更新后的完整文档。"""

    result = chat_with_tool(
        llm=llm,
        system_prompt=system,
        user_content=f"## 影响分析\n\n{impact_text}\n\n## 当前 spec.md\n\n{current_spec_md}",
        tool_name="update_spec",
        tool_description="返回更新后的完整 spec.md 内容",
        tool_parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "更新后的完整 spec.md 内容"},
            },
            "required": ["content"],
        },
    )

    if result and result.get("content"):
        return result["content"]
    return current_spec_md


# =============================================================================
# Verification
# =============================================================================

def verify_evolution(
    old_design_md: str,
    new_design_md: str,
    impact: "ImpactAnalysis",
) -> list[str]:
    """Verify that unchanged modules/interfaces survived the evolution.

    Compares old and new design.md to ensure nothing was accidentally dropped.
    Returns list of warning strings.
    """
    warnings = []

    # Extract module names from both versions
    old_modules = _extract_module_names(old_design_md)
    new_modules = _extract_module_names(new_design_md)

    # Build set of affected module names
    affected_names = set()
    for m in impact.affected_modules:
        affected_names.add(m.module_name)
    deprecated_names = set()
    for m in impact.affected_modules:
        if m.change_type == "deprecate":
            deprecated_names.add(m.module_name)

    # Check unchanged modules are preserved
    for name in old_modules:
        if name not in affected_names and name not in new_modules:
            warnings.append(f"模块 {name} 在更新后的 design.md 中丢失")

    # Extract interface names
    old_interfaces = _extract_interface_names(old_design_md)
    new_interfaces = _extract_interface_names(new_design_md)

    affected_intf_names = set()
    for i in impact.affected_interfaces:
        affected_intf_names.add(i.interface_name)

    for name in old_interfaces:
        if name not in affected_intf_names and name not in new_interfaces:
            warnings.append(f"接口 {name} 在更新后的 design.md 中丢失")

    return warnings


def _extract_module_names(design_md: str) -> set[str]:
    """Extract module names from design.md markdown."""
    names = set()
    for m in re.finditer(r"####\s+(\w+)\s*\(", design_md):
        names.add(m.group(1))
    # Also match "## Module: Name" format
    for m in re.finditer(r"##\s+Module:\s*(\w+)", design_md):
        names.add(m.group(1))
    return names


def _extract_interface_names(design_md: str) -> set[str]:
    """Extract interface names from design.md markdown."""
    names = set()
    # Format: "#### name (id)"
    for m in re.finditer(r"####\s+(\w+)\s*\(", design_md):
        names.add(m.group(1))
    # Format: "### Function: name" or "### Class: name"
    for m in re.finditer(r"###\s+(?:Function|Class):\s*(\w+)", design_md):
        names.add(m.group(1))
    # Format: "- **Signature:** `def name("
    for m in re.finditer(r"def\s+(\w+)\s*\(", design_md):
        names.add(m.group(1))
    # Format: "class Name"
    for m in re.finditer(r"class\s+(\w+)", design_md):
        names.add(m.group(1))
    return names


def _build_unchanged_note(design_md: str, impact: "ImpactAnalysis") -> str:
    """Build a note listing unchanged modules/interfaces for the LLM prompt."""
    affected_ids = set()
    for m in impact.affected_modules:
        affected_ids.add(m.module_id)
    for i in impact.affected_interfaces:
        affected_ids.add(i.interface_id)

    lines = ["以下模块和接口未受影响，必须在输出中原样保留："]

    # Extract module sections
    module_names = _extract_module_names(design_md)
    interface_names = _extract_interface_names(design_md)

    affected_names = set()
    for m in impact.affected_modules:
        affected_names.add(m.module_name)
    for n in impact.new_modules:
        affected_names.add(n.name)
    for i in impact.affected_interfaces:
        affected_names.add(i.interface_name)

    unchanged_modules = module_names - affected_names
    unchanged_interfaces = interface_names - affected_names

    if unchanged_modules:
        lines.append(f"\n未变更模块: {', '.join(sorted(unchanged_modules))}")
    if unchanged_interfaces:
        lines.append(f"未变更接口: {', '.join(sorted(unchanged_interfaces))}")

    if not unchanged_modules and not unchanged_interfaces:
        lines.append("（所有模块和接口都受到影响）")

    return "\n".join(lines)
