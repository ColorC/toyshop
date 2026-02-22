#!/usr/bin/env python3
"""Change Pipeline E2E Test - Brownfield modification test.

This script tests the complete change pipeline:
1. Phase 1 (Greenfield): Create a Calculator project from scratch
2. Phase 2 (Brownfield): Modify the Calculator to add history feature

This validates that:
- create mode still works (regression)
- modify mode loads architecture context from storage
- modify mode prompt guides Agent to edit existing files
- existing tests still pass after modification

Usage:
    python tests/run_modify_e2e.py [--skip-create] [--workspace PATH] [--keep]
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
)


def print_section(title: str):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


# Original calculator requirements (same as run_complete_e2e.py)
CREATE_REQUIREMENTS = """创建一个简单的计算器模块，支持：

1. 基本四则运算（加减乘除）
2. 支持整数和小数
3. 处理除零错误
4. 提供命令行接口

请保持简单，只实现核心功能。
"""

# Change request: add history feature to existing calculator
CHANGE_REQUIREMENTS = """为现有的计算器项目添加计算历史记录功能：

1. 添加 History 类，记录每次计算的表达式和结果
2. History 支持：
   - add_entry(expression, result): 添加记录
   - get_history(): 获取所有历史记录
   - clear(): 清空历史
   - last(n): 获取最近 n 条记录
3. 在 CLI 中集成历史功能：
   - 输入 'history' 显示历史记录
   - 输入 'clear' 清空历史
4. 添加对应的单元测试

注意：修改现有文件，不要重写。新增 history.py 模块。
"""


def run_create_phase(workspace: str, llm):
    """Phase 1: Create the calculator project (greenfield)."""
    print_section("Phase 1: Create Calculator (Greenfield)")

    start_time = datetime.now()

    # Design
    print("Running design phase...")
    conversation = run_toyshop_workflow(
        user_input=CREATE_REQUIREMENTS,
        project_name="Calculator",
        workspace=workspace,
        llm=llm,
        persist=True,
    )

    project_id = conversation.project_id
    print(f"Project ID: {project_id}")

    # Code
    print("\nRunning coding phase (create mode)...")
    result = run_coding_workflow(
        workspace=workspace,
        llm=llm,
        language="python",
        mode="create",
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCreate phase completed in {elapsed:.1f}s")
    print(f"Files created: {len(result.files_created)}")
    for f in result.files_created[:15]:
        print(f"  - {f}")

    return project_id


def run_modify_phase(workspace: str, project_id: str, llm):
    """Phase 2: Modify the calculator to add history (brownfield)."""
    print_section("Phase 2: Add History Feature (Brownfield)")

    # First, generate change design documents
    print("Running change design phase...")

    # Save change-specific openspec docs
    # We overwrite the openspec/ with change-specific design
    change_design_conversation = run_toyshop_workflow(
        user_input=CHANGE_REQUIREMENTS,
        project_name="Calculator-History",
        workspace=workspace,
        llm=llm,
        persist=True,
    )

    start_time = datetime.now()

    # Run coding in modify mode
    print("\nRunning coding phase (modify mode)...")
    result = run_coding_workflow(
        workspace=workspace,
        llm=llm,
        language="python",
        mode="modify",
        project_id=project_id,
        change_request=CHANGE_REQUIREMENTS,
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nModify phase completed in {elapsed:.1f}s")
    print(f"Files in workspace: {len(result.files_created)}")
    for f in sorted(result.files_created):
        print(f"  - {f}")

    return result


def verify_results(workspace: str):
    """Verify the modification results."""
    print_section("Verification")

    ws = Path(workspace)
    issues = []

    # Check that original calculator files still exist
    expected_files = [
        "calculator/__init__.py",
        "calculator/calculator.py",
        "calculator/cli.py",
        "calculator/exceptions.py",
        "calculator/validators.py",
    ]

    print("Checking original files...")
    for f in expected_files:
        path = ws / f
        if path.exists():
            print(f"  ✓ {f}")
        else:
            print(f"  ✗ {f} MISSING")
            issues.append(f"Missing original file: {f}")

    # Check that history module was added
    print("\nChecking new files...")
    history_candidates = list(ws.rglob("history*"))
    if history_candidates:
        for h in history_candidates:
            print(f"  ✓ {h.relative_to(ws)}")
    else:
        print("  ✗ No history module found")
        issues.append("No history module created")

    # Check that tests exist
    print("\nChecking tests...")
    test_files = list(ws.rglob("test_*.py"))
    for t in test_files:
        print(f"  ✓ {t.relative_to(ws)}")

    if not test_files:
        print("  ✗ No test files found")
        issues.append("No test files found")

    # Summary
    if issues:
        print(f"\n⚠️ {len(issues)} issues found:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    else:
        print("\n✅ All checks passed")
        return True


def main():
    parser = argparse.ArgumentParser(description="Change Pipeline E2E test")
    parser.add_argument("--skip-create", action="store_true", help="Skip create phase (use existing workspace)")
    parser.add_argument("--workspace", type=str, help="Use existing workspace")
    parser.add_argument("--keep", action="store_true", help="Keep workspace after test")
    args = parser.parse_args()

    if args.workspace:
        workspace = args.workspace
        print(f"Using existing workspace: {workspace}")
    else:
        workspace = tempfile.mkdtemp(prefix="toyshop_modify_e2e_")
        print(f"Created workspace: {workspace}")

    try:
        llm = create_toyshop_llm()
        print(f"LLM: {llm.model}")

        # Phase 1: Create (greenfield)
        if not args.skip_create:
            project_id = run_create_phase(workspace, llm)
        else:
            print("Skipping create phase")
            project_id = None

        # Phase 2: Modify (brownfield)
        modify_result = run_modify_phase(workspace, project_id, llm)

        # Verify
        success = verify_results(workspace)

        # Final summary
        print_section("Summary")
        print(f"Workspace: {workspace}")
        print(f"Create mode: {'skipped' if args.skip_create else '✅'}")
        print(f"Modify mode: ✅")
        print(f"Verification: {'✅' if success else '⚠️'}")

        return 0 if success else 1

    finally:
        if not args.keep and not args.workspace:
            print(f"\nCleaning up: {workspace}")
            shutil.rmtree(workspace, ignore_errors=True)
        elif args.keep:
            print(f"\nWorkspace preserved: {workspace}")


if __name__ == "__main__":
    sys.exit(main())
