#!/usr/bin/env python3
"""TDD Pipeline E2E Test.

Tests the complete TDD pipeline:
1. Design phase: Generate openspec/ documents for a Calculator project
2. TDD Phase 1: Signature extraction → stub files
3. TDD Phase 2: Test Agent writes tests (restricted to tests/)
4. TDD Phase 3: Code Agent implements code (blocked from tests/)
5. TDD Phase 4: White-box verification (read-only agent)
6. TDD Phase 5: Black-box verification (from spec.md scenarios)

Usage:
    python tests/run_tdd_e2e.py [--skip-design] [--workspace PATH] [--keep]
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
)
from toyshop.tdd_pipeline import (
    extract_signatures,
    generate_blackbox_tests,
    run_tdd_pipeline,
    TDDResult,
)


def print_section(title: str):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


# Calculator requirements (same as other E2E tests)
REQUIREMENTS = """创建一个简单的计算器模块，支持：

1. 基本四则运算（加减乘除）
2. 支持整数和小数
3. 处理除零错误
4. 提供命令行接口

请保持简单，只实现核心功能。
"""


def run_design_phase(workspace: str, llm):
    """Run the design phase to generate openspec/ documents."""
    print_section("Design Phase")

    start = datetime.now()
    conversation = run_toyshop_workflow(
        user_input=REQUIREMENTS,
        project_name="Calculator",
        workspace=workspace,
        llm=llm,
        persist=True,
    )
    elapsed = (datetime.now() - start).total_seconds()
    print(f"Design completed in {elapsed:.1f}s")

    # Verify openspec files
    ws = Path(workspace)
    for doc in ["proposal.md", "design.md", "tasks.md", "spec.md"]:
        path = ws / "openspec" / doc
        if path.exists():
            print(f"  ✓ {doc} ({path.stat().st_size} bytes)")
        else:
            print(f"  ✗ {doc} MISSING")

    return conversation


def run_tdd_phase(workspace: str, llm) -> TDDResult:
    """Run the full TDD pipeline."""
    print_section("TDD Pipeline")

    start = datetime.now()
    result = run_tdd_pipeline(
        workspace=workspace,
        llm=llm,
        language="python",
    )
    elapsed = (datetime.now() - start).total_seconds()

    print(f"\nTDD pipeline completed in {elapsed:.1f}s")
    print(f"  Success: {result.success}")
    print(f"  White-box: {'PASSED' if result.whitebox_passed else 'FAILED'}")
    print(f"  Black-box: {'PASSED' if result.blackbox_passed else 'FAILED'}")
    print(f"  Retries: {result.retry_count}")
    print(f"  Debug reports: {len(result.debug_reports)}")
    print(f"  Legacy issues: {len(result.legacy_issues)}")
    print(f"  Stub files: {result.stub_files}")
    print(f"  Test files: {result.test_files}")
    print(f"  Files created: {len(result.files_created)}")
    for f in sorted(result.files_created)[:20]:
        print(f"    - {f}")

    # Print debug report summaries
    for i, dr in enumerate(result.debug_reports):
        print(f"\n  Debug Report #{i+1}:")
        print(f"    Failing tests: {dr.failing_tests}")
        print(f"    Hypotheses: {len(dr.hypotheses)} active, {len(dr.excluded_hypotheses)} excluded")
        for h in dr.hypotheses:
            print(f"      [{h.status}] {h.id}: {h.description[:80]}")

    # Print legacy issues
    for issue in result.legacy_issues:
        print(f"\n  Legacy Issue: {issue.test_name}")
        print(f"    Status: {issue.final_status}")
        print(f"    Attempts: {len(issue.all_attempts)}")

    return result


def verify_results(workspace: str, result: TDDResult) -> bool:
    """Verify the TDD pipeline results."""
    print_section("Verification")

    ws = Path(workspace)
    issues = []

    # 1. Check stub files were generated
    print("Checking stub files...")
    for stub in result.stub_files:
        path = ws / stub
        if path.exists():
            print(f"  ✓ {stub}")
        else:
            print(f"  ✗ {stub} MISSING")
            issues.append(f"Missing stub: {stub}")

    # 2. Check test files were created
    print("\nChecking test files...")
    test_files = list((ws / "tests").rglob("test_*.py"))
    if test_files:
        for t in test_files:
            print(f"  ✓ {t.relative_to(ws)}")
    else:
        print("  ✗ No test files found")
        issues.append("No test files created")

    # 3. Check implementation files exist (not just stubs)
    print("\nChecking implementation files...")
    py_files = [
        f for f in ws.rglob("*.py")
        if not str(f.relative_to(ws)).startswith(("tests", "openspec", ".toyshop", "__pycache__"))
    ]
    if py_files:
        for f in py_files:
            content = f.read_text(encoding="utf-8")
            has_impl = "NotImplementedError" not in content or "def " in content
            status = "✓" if has_impl else "⚠ (still stub)"
            print(f"  {status} {f.relative_to(ws)}")
    else:
        print("  ✗ No implementation files found")
        issues.append("No implementation files")

    # 4. Check black-box test file
    print("\nChecking black-box tests...")
    bb_path = ws / "tests" / "test_blackbox_auto.py"
    if bb_path.exists():
        print(f"  ✓ {bb_path.relative_to(ws)}")
    else:
        print("  ⚠ No black-box test file (spec.md may have no scenarios)")

    # 5. Check pipeline result
    print("\nChecking pipeline result...")
    if result.success:
        print("  ✓ Pipeline succeeded")
    else:
        print(f"  ✗ Pipeline failed: {result.summary}")
        issues.append(f"Pipeline failed: {result.summary}")

    if result.whitebox_passed:
        print("  ✓ White-box tests passed")
    else:
        print("  ✗ White-box tests failed")
        issues.append("White-box tests failed")

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
    parser = argparse.ArgumentParser(description="TDD Pipeline E2E test")
    parser.add_argument("--skip-design", action="store_true", help="Skip design phase (use existing workspace)")
    parser.add_argument("--workspace", type=str, help="Use existing workspace")
    parser.add_argument("--keep", action="store_true", help="Keep workspace after test")
    args = parser.parse_args()

    if args.workspace:
        workspace = args.workspace
        print(f"Using existing workspace: {workspace}")
    else:
        workspace = tempfile.mkdtemp(prefix="toyshop_tdd_e2e_")
        print(f"Created workspace: {workspace}")

    try:
        llm = create_toyshop_llm()
        print(f"LLM: {llm.model}")

        # Design phase
        if not args.skip_design:
            run_design_phase(workspace, llm)
        else:
            print("Skipping design phase")

        # TDD pipeline
        result = run_tdd_phase(workspace, llm)

        # Verify
        success = verify_results(workspace, result)

        # Final summary
        print_section("Summary")
        print(f"Workspace: {workspace}")
        print(f"Design: {'skipped' if args.skip_design else '✅'}")
        print(f"TDD Pipeline: {'✅' if result.success else '⚠️'}")
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
