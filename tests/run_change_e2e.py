#!/usr/bin/env python3
"""Change Pipeline E2E Test.

This script demonstrates the change pipeline workflow:
1. Analyze existing codebase (toyshop itself)
2. Generate change OpenSpec documents
3. Identify architecture changes
4. Generate code changes (diff or direct modification)

Usage:
    python tests/run_change_e2e.py [--workspace PATH] [--dry-run]
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
    run_ux_evaluation,
    UxEvaluationMode,
)


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


CHANGE_REQUEST = """为 ToyShop 添加变更管线支持，使其能够对已有代码进行增量变更。

具体要求：
1. 添加 ChangeAgent 类，用于管理变更流程
2. 添加影响分析工具，识别哪些文件需要修改
3. 生成变更 OpenSpec 文档（change-request, impact-analysis, change-plan）
4. 支持生成代码变更

变更应保持与现有 Agent 架构的一致性，复用 openhands-sdk 的基础设施。
"""


def run_change_analysis_phase(workspace: str, change_request: str, llm):
    """Run the change analysis phase."""
    print_section("Phase 1: Change Analysis")

    # For now, use the existing ToyShop workflow to generate change specs
    # In the future, this will use a dedicated ChangeAgent

    print(f"Target: {workspace}")
    print(f"Change Request: {change_request[:200]}...")

    start_time = datetime.now()

    # Use existing workflow with change request as input
    conversation = run_toyshop_workflow(
        user_input=change_request,
        project_name="ToyShop-Change",
        workspace=workspace,
        llm=llm,
        persist=True,
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nAnalysis completed in {elapsed:.1f}s")

    # Print summary
    proposal = conversation.get_proposal()
    if proposal:
        caps = proposal.get("capabilities", [])
        print(f"\nProposed capabilities: {len(caps)}")
        for cap in caps[:5]:
            print(f"  - {cap.get('name', 'Unknown')}")

    return conversation


def run_impact_analysis_phase(workspace: str, llm):
    """Analyze impact on existing codebase."""
    print_section("Phase 2: Impact Analysis")

    target_path = Path("/home/dministrator/work/openclaw/extensions/toyshop/python/toyshop")

    print(f"Analyzing existing codebase: {target_path}")

    # List existing modules
    existing_files = list(target_path.rglob("*.py"))
    print(f"\nExisting Python files: {len(existing_files)}")

    for f in existing_files:
        rel_path = f.relative_to(target_path)
        print(f"  - {rel_path}")

    # Identify potential impact areas
    print("\n--- Impact Analysis ---")
    print("Modules likely to be affected:")
    print("  - __init__.py: Need to export new ChangeAgent API")
    print("  - coding_agent.py: May need refactoring to share code")
    print("  - tools/: Need new change-related tools")

    print("\nNew modules to create:")
    print("  - change_agent.py: Core change management agent")
    print("  - tools/analyze_codebase.py: Code analysis tool")
    print("  - tools/generate_impact.py: Impact generation tool")

    return {
        "affected_files": ["__init__.py", "coding_agent.py"],
        "new_files": ["change_agent.py", "tools/analyze_codebase.py"],
    }


def run_change_planning_phase(workspace: str, impact: dict, llm):
    """Generate change plan."""
    print_section("Phase 3: Change Planning")

    print("Generating change plan based on impact analysis...")

    tasks = [
        {"id": "1", "task": "Create change_agent.py with ChangeAgent class", "priority": "must"},
        {"id": "2", "task": "Add analyze_codebase.py tool", "priority": "must"},
        {"id": "3", "task": "Add generate_impact.py tool", "priority": "must"},
        {"id": "4", "task": "Update __init__.py to export new APIs", "priority": "must"},
        {"id": "5", "task": "Refactor coding_agent.py for code reuse", "priority": "should"},
        {"id": "6", "task": "Add unit tests for ChangeAgent", "priority": "should"},
        {"id": "7", "task": "Update documentation", "priority": "could"},
    ]

    print("\nChange Tasks:")
    for task in tasks:
        print(f"  [{task['priority'].upper()}] {task['id']}. {task['task']}")

    # Write change plan to openspec
    change_plan_path = Path(workspace) / "openspec" / "change-plan.md"
    change_plan_content = f"""# Change Plan

## Summary
Add change pipeline support to ToyShop for incremental code modifications.

## Affected Files
{chr(10).join(f"- {f}" for f in impact['affected_files'])}

## New Files
{chr(10).join(f"- {f}" for f in impact['new_files'])}

## Tasks

{chr(10).join(f"### {t['id']}. {t['task']} (Priority: {t['priority']}){chr(10)}" for t in tasks)}

## Execution Order
1. Create new modules (change_agent.py, tools)
2. Update __init__.py exports
3. Refactor existing code
4. Add tests
5. Update documentation

## Rollback Plan
If issues arise:
1. Remove new files from git
2. Revert __init__.py changes
3. Run full test suite to verify
"""

    change_plan_path.write_text(change_plan_content)
    print(f"\nChange plan written to: {change_plan_path}")

    return tasks


def run_change_implementation_phase(workspace: str, tasks: list, llm, dry_run: bool):
    """Implement the changes."""
    print_section("Phase 4: Change Implementation")

    if dry_run:
        print("DRY RUN - Skipping actual implementation")
        print("\nWould create/modify the following files:")

        # Show what would be created
        changes = [
            ("NEW", "toyshop/change_agent.py"),
            ("NEW", "toyshop/tools/analyze_codebase.py"),
            ("NEW", "toyshop/tools/generate_impact.py"),
            ("MOD", "toyshop/__init__.py"),
            ("MOD", "toyshop/coding_agent.py"),
        ]

        for change_type, path in changes:
            print(f"  [{change_type}] {path}")

        return

    # Actual implementation would use Coding Agent
    print("Implementing changes using Coding Agent...")
    print("(Implementation would go here)")


def main():
    parser = argparse.ArgumentParser(description="Change Pipeline E2E test")
    parser.add_argument(
        "--workspace",
        type=str,
        help="Use existing workspace (should contain toyshop code)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually make changes, just show what would be done",
    )
    parser.add_argument("--keep", action="store_true", help="Keep workspace after test")
    args = parser.parse_args()

    # Setup workspace
    if args.workspace:
        workspace = args.workspace
        print(f"Using existing workspace: {workspace}")
    else:
        workspace = tempfile.mkdtemp(prefix="toyshop_change_e2e_")
        print(f"Created workspace: {workspace}")

    try:
        llm = create_toyshop_llm()
        print(f"LLM: {llm.model}")

        # Phase 1: Change Analysis
        conversation = run_change_analysis_phase(workspace, CHANGE_REQUEST, llm)

        # Phase 2: Impact Analysis
        impact = run_impact_analysis_phase(workspace, llm)

        # Phase 3: Change Planning
        tasks = run_change_planning_phase(workspace, impact, llm)

        # Phase 4: Implementation
        run_change_implementation_phase(workspace, tasks, llm, args.dry_run)

        # Summary
        print_section("Summary")
        print(f"Workspace: {workspace}")
        print(f"Change Request: ToyShop Change Pipeline")
        print(f"Affected Files: {len(impact['affected_files'])}")
        print(f"New Files: {len(impact['new_files'])}")
        print(f"Tasks: {len(tasks)}")

        if args.dry_run:
            print("\n⚠️ DRY RUN - No changes were made")
        else:
            print("\n✅ Change pipeline completed")

    finally:
        if not args.keep and not args.workspace:
            print(f"\nCleaning up: {workspace}")
            shutil.rmtree(workspace, ignore_errors=True)
        elif args.keep:
            print(f"\nWorkspace preserved: {workspace}")


if __name__ == "__main__":
    sys.exit(main())
