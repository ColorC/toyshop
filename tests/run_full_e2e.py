#!/usr/bin/env python3
"""Full E2E test for ToyShop workflow + UX Agent evaluation.

This script demonstrates the complete workflow:
1. ToyShop Agent designs a software project from requirements
2. UX Agent evaluates the quality of generated artifacts

Usage:
    python tests/run_full_e2e.py [--skip-design]

Run with --skip-design to skip the design phase and use existing artifacts.
"""

import argparse
import json
import tempfile
import shutil
import sys
from pathlib import Path
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from toyshop import (
    create_toyshop_llm,
    create_toyshop_agent,
    ToyShopConversation,
    run_toyshop_workflow,
    create_ux_agent,
    run_ux_evaluation as run_ux_agent_evaluation,
    UxEvaluationMode,
)


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def run_design_phase(workspace: str, requirements: str, project_name: str) -> ToyShopConversation:
    """Run the ToyShop design phase."""
    print_section(f"Phase 1: Design - {project_name}")

    print(f"Workspace: {workspace}")
    print(f"Requirements: {requirements[:100]}...")

    llm = create_toyshop_llm()
    print(f"LLM created: {llm.model}")

    print("\nStarting ToyShop workflow...")
    start_time = datetime.now()

    conversation = run_toyshop_workflow(
        user_input=requirements,
        project_name=project_name,
        workspace=workspace,
        llm=llm,
        persist=True,
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nWorkflow completed in {elapsed:.1f}s")

    # Print artifacts summary
    context = conversation.get_context()

    proposal = conversation.get_proposal()
    if proposal:
        caps = proposal.get("capabilities", [])
        print(f"\nProposal: {len(caps)} capabilities")

    design = conversation.get_design()
    if design:
        modules = design.get("modules", [])
        interfaces = design.get("interfaces", [])
        print(f"Design: {len(modules)} modules, {len(interfaces)} interfaces")

    tasks = conversation.get_tasks()
    if tasks:
        print(f"Tasks: {len(tasks)} tasks")

    spec = conversation.get_spec()
    if spec:
        scenarios = spec.get("scenarios", [])
        print(f"Spec: {len(scenarios)} test scenarios")

    # Check persistence
    if conversation.project_id:
        print(f"\nPersisted to database:")
        print(f"  Project ID: {conversation.project_id}")
        print(f"  Snapshot ID: {conversation.snapshot_id}")

    return conversation


def run_ux_evaluation(workspace: str, task_description: str) -> dict:
    """Run UX Agent evaluation."""
    print_section("Phase 2: UX Evaluation")

    print(f"Target workspace: {workspace}")
    print(f"Task: {task_description}")

    llm = create_toyshop_llm()
    print(f"LLM created: {llm.model}")

    # Enhance task description with explicit file paths
    enhanced_task = f"""{task_description}

请按以下步骤评估：
1. 使用 file_read 读取以下文件：
   - openspec/proposal.md（需求提案）
   - openspec/design.md（架构设计）
   - openspec/tasks.md（任务分解）
   - openspec/spec.md（测试规格）
2. 对照需求评估每个文档的覆盖度和质量
3. 使用 report_builder 填写评估字段并生成报告
4. 调用 finish 提交结果
"""

    print("\nStarting UX evaluation...")
    start_time = datetime.now()

    result = run_ux_agent_evaluation(
        target_workspace=workspace,
        task_description=enhanced_task,
        llm=llm,
        mode=UxEvaluationMode.E2E,
        max_iterations=15,
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nEvaluation completed in {elapsed:.1f}s")

    print(f"\nResult:")
    print(f"  Finished: {result.finished}")
    print(f"  Assessment Level: {result.assessment_level}/5")
    print(f"  Summary: {result.summary[:100]}...")

    return {
        "finished": result.finished,
        "assessment_level": result.assessment_level,
        "summary": result.summary,
        "report": result.report,
        "iterations": result.iterations,
    }


def main():
    parser = argparse.ArgumentParser(description="Full E2E test")
    parser.add_argument("--skip-design", action="store_true", help="Skip design phase")
    parser.add_argument("--workspace", type=str, help="Use existing workspace")
    parser.add_argument("--keep", action="store_true", help="Keep workspace after test")
    args = parser.parse_args()

    # Test requirements - Stock Analyzer from pipelines extension
    requirements = """创建一个股票分析器应用，具有以下功能：

1. 股票数据获取
   - 从公开 API 获取实时股票价格
   - 支持获取历史价格数据
   - 获取股票基本信息（名称、代码、市值等）

2. 技术指标计算
   - 移动平均线（MA5, MA10, MA20）
   - 相对强弱指标（RSI）
   - MACD 指标
   - 布林带

3. 数据可视化
   - K线图展示
   - 技术指标图表
   - 成交量柱状图

4. 简单分析报告
   - 基于技术指标的买入/卖出建议
   - 趋势分析
   - 风险提示

应用应该是命令行工具，支持指定股票代码进行分析。
"""

    project_name = "StockAnalyzer"

    # Setup workspace
    if args.workspace:
        workspace = args.workspace
        print(f"Using existing workspace: {workspace}")
    else:
        workspace = tempfile.mkdtemp(prefix="toyshop_full_e2e_")
        print(f"Created workspace: {workspace}")

    try:
        # Phase 1: Design
        if not args.skip_design:
            conversation = run_design_phase(workspace, requirements, project_name)

            # Show generated files
            print_section("Generated Artifacts")
            openspec_dir = Path(workspace) / "openspec"
            if openspec_dir.exists():
                for f in openspec_dir.iterdir():
                    print(f"  {f.name}: {f.stat().st_size} bytes")
        else:
            print("Skipping design phase (--skip-design)")

        # Phase 2: UX Evaluation
        ux_result = run_ux_evaluation(
            workspace,
            f"评估 {project_name} 项目的设计质量，检查需求覆盖度和架构合理性"
        )

        # Print UX report
        print_section("UX Evaluation Report")
        print(ux_result["report"])

        # Final summary
        print_section("Summary")
        print(f"Workspace: {workspace}")
        print(f"Design Phase: {'Skipped' if args.skip_design else 'Completed'}")
        print(f"UX Assessment: {ux_result['assessment_level']}/5")

        # assessment_level may be string or int, convert to int for comparison
        level = int(ux_result["assessment_level"])

        # Note: UX Agent uses 1=worst, 5=best (opposite of original plan)
        # So higher is better
        if level >= 4:
            print("\n✅ Test PASSED - Good quality output")
            return 0
        elif level >= 3:
            print("\n⚠️ Test ACCEPTABLE - Room for improvement")
            return 0
        else:
            print("\n❌ Test FAILED - Quality issues detected")
            return 1

    finally:
        if not args.keep and not args.workspace:
            print(f"\nCleaning up workspace: {workspace}")
            shutil.rmtree(workspace, ignore_errors=True)
        elif args.keep:
            print(f"\nWorkspace preserved: {workspace}")


if __name__ == "__main__":
    sys.exit(main())
