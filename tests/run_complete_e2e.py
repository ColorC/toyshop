#!/usr/bin/env python3
"""Full E2E test with Design → Code → Test pipeline.

This script demonstrates the complete workflow:
1. ToyShop Agent designs a project from requirements
2. Coding Agent generates code based on design documents
3. Tests are executed to validate implementation
4. UX Agent evaluates the final output

Usage:
    python tests/run_complete_e2e.py [--skip-design] [--workspace PATH]
"""

import argparse
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from toyshop import (
    create_toyshop_llm,
    run_toyshop_workflow,
    run_coding_workflow,
    run_ux_evaluation,
    UxEvaluationMode,
    CodingResult,
)


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def run_design_phase(workspace: str, requirements: str, project_name: str, llm):
    """Run the ToyShop design phase."""
    print_section(f"Phase 1: Design - {project_name}")

    print(f"Workspace: {workspace}")
    print(f"Requirements preview: {requirements[:200]}...")

    start_time = datetime.now()

    conversation = run_toyshop_workflow(
        user_input=requirements,
        project_name=project_name,
        workspace=workspace,
        llm=llm,
        persist=True,
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nDesign completed in {elapsed:.1f}s")

    # Print summary
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

    # Check files
    openspec_dir = Path(workspace) / "openspec"
    if openspec_dir.exists():
        print("\nGenerated documents:")
        for f in openspec_dir.iterdir():
            print(f"  - {f.name}: {f.stat().st_size} bytes")

    return conversation


def run_coding_phase(workspace: str, llm, language: str = "python"):
    """Run the coding phase."""
    print_section("Phase 2: Code Generation")

    print(f"Workspace: {workspace}")
    print(f"Language: {language}")

    start_time = datetime.now()

    result = run_coding_workflow(
        workspace=workspace,
        llm=llm,
        language=language,
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCoding completed in {elapsed:.1f}s")

    print(f"\nFiles created: {len(result.files_created)}")
    for f in result.files_created[:20]:  # Show first 20
        print(f"  - {f}")
    if len(result.files_created) > 20:
        print(f"  ... and {len(result.files_created) - 20} more")

    return result


def run_evaluation_phase(workspace: str, project_name: str, llm):
    """Run UX evaluation phase."""
    print_section("Phase 3: UX Evaluation")

    print(f"Target: {workspace}")

    # Enhanced task with explicit file paths
    task = f"""评估 {project_name} 项目的完整实现质量。

请按以下步骤评估：
1. 使用 file_read 读取设计文档：
   - openspec/proposal.md
   - openspec/design.md
   - openspec/tasks.md
   - openspec/spec.md
2. 检查生成的代码文件
3. 评估代码与设计的一致性
4. 使用 report_builder 生成报告
"""

    start_time = datetime.now()

    result = run_ux_evaluation(
        target_workspace=workspace,
        task_description=task,
        llm=llm,
        mode=UxEvaluationMode.E2E,
        max_iterations=15,
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nEvaluation completed in {elapsed:.1f}s")

    print(f"\nAssessment: {result.assessment_level}/5")
    print(f"Summary: {result.summary[:200]}...")

    return result


def main():
    parser = argparse.ArgumentParser(description="Complete E2E test")
    parser.add_argument("--skip-design", action="store_true", help="Skip design phase")
    parser.add_argument("--skip-coding", action="store_true", help="Skip coding phase")
    parser.add_argument("--workspace", type=str, help="Use existing workspace")
    parser.add_argument("--keep", action="store_true", help="Keep workspace after test")
    parser.add_argument("--language", type=str, default="python", help="Target language")
    args = parser.parse_args()

    # Test requirements - Simple calculator for faster testing
    requirements = """创建一个简单的计算器模块，支持：

1. 基本四则运算（加减乘除）
2. 支持整数和小数
3. 处理除零错误
4. 提供命令行接口

请保持简单，只实现核心功能。
"""

    project_name = "Calculator"

    # Setup workspace
    if args.workspace:
        workspace = args.workspace
        print(f"Using existing workspace: {workspace}")
    else:
        workspace = tempfile.mkdtemp(prefix="toyshop_complete_e2e_")
        print(f"Created workspace: {workspace}")

    try:
        llm = create_toyshop_llm()
        print(f"LLM: {llm.model}")

        # Phase 1: Design
        if not args.skip_design:
            run_design_phase(workspace, requirements, project_name, llm)
        else:
            print("Skipping design phase")

        # Phase 2: Coding
        if not args.skip_coding:
            coding_result = run_coding_phase(workspace, llm, args.language)
        else:
            print("Skipping coding phase")

        # Phase 3: Evaluation
        eval_result = run_evaluation_phase(workspace, project_name, llm)

        # Print final report
        print_section("Final Report")
        print(eval_result.report)

        # Summary
        print_section("Summary")
        print(f"Workspace: {workspace}")
        print(f"Project: {project_name}")
        print(f"Language: {args.language}")
        print(f"UX Assessment: {eval_result.assessment_level}/5")

        level = int(eval_result.assessment_level)
        if level >= 4:
            print("\n✅ Pipeline PASSED")
            return 0
        elif level >= 3:
            print("\n⚠️ Pipeline ACCEPTABLE")
            return 0
        else:
            print("\n❌ Pipeline NEEDS IMPROVEMENT")
            return 1

    finally:
        if not args.keep and not args.workspace:
            print(f"\nCleaning up: {workspace}")
            shutil.rmtree(workspace, ignore_errors=True)
        elif args.keep:
            print(f"\nWorkspace preserved: {workspace}")


if __name__ == "__main__":
    sys.exit(main())
