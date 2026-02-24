#!/usr/bin/env python3
"""Phased pipeline E2E test with workflow tracking.

Tests the phased pipeline (research → MVP) with real LLM and records
the workflow run to the database.

Usage:
    TOYSHOP_RUN_LIVE_E2E=1 python tests/run_phased_e2e.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from toyshop.llm import create_llm, probe_llm
from toyshop.pm import run_batch_phased
from toyshop.storage.database import init_database, close_database
from toyshop.self_host import record_pipeline_run


_LLM_ERROR_PATTERNS = [
    "ServiceUnavailableError", "No available accounts", "APIConnectionError",
    "AuthenticationError", "RateLimitError", "BadGatewayError",
    "Upstream request failed", "Connection refused", "Timeout Error",
]


def check_env():
    if not os.environ.get("TOYSHOP_RUN_LIVE_E2E"):
        print("Skipping: set TOYSHOP_RUN_LIVE_E2E=1 to run")
        return False
    return True


def print_section(title: str):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def main():
    if not check_env():
        return 0

    print_section("Phased Pipeline E2E Test")

    # Create LLM and probe
    print("\n[Step 1] Initializing LLM...")
    llm = create_llm()

    ok, msg = probe_llm(llm, timeout=15)
    if not ok:
        print(f"  ✗ LLM unavailable: {msg}")
        return 1
    print(f"  ✓ LLM ready")

    # Use temp directory for this test
    with tempfile.TemporaryDirectory() as tmpdir:
        pm_root = Path(tmpdir) / "pm"
        db_path = Path(tmpdir) / "test.db"
        init_database(db_path)

        # Simple requirements
        requirements = """创建一个简单的字符串工具库：
1. reverse(s) - 反转字符串
2. is_palindrome(s) - 检查是否回文
3. count_words(s) - 统计单词数
保持简单，只实现核心功能。
"""

        print_section("Running Phased Pipeline (MVP only)")
        print(f"Requirements: {requirements[:60]}...")

        start = datetime.now()

        # Run phased pipeline (MVP only, no SOTA)
        batch = run_batch_phased(
            pm_root=pm_root,
            project_name="StringUtils",
            user_input=requirements,
            llm=llm,
            project_type="python",
            auto_continue_sota=False,  # Stop after MVP
            enable_research_agent=False,  # Skip research for speed
        )

        elapsed = (datetime.now() - start).total_seconds()

        print(f"\n  Status: {batch.status}")
        print(f"  Elapsed: {elapsed:.1f}s")

        if batch.error:
            print(f"  Error: {batch.error}")

        # Record the workflow run
        print_section("Recording Workflow Run")
        run_id = record_pipeline_run(
            project_id="test-project",
            workflow_type="tdd_create",
            batch_id=batch.batch_id,
            result={
                "success": batch.status == "completed",
                "elapsed_seconds": elapsed,
            },
            status="completed" if batch.status == "completed" else "failed",
        )
        print(f"  ✓ Workflow run ID: {run_id}")

        # Check generated files
        print_section("Generated Artifacts")
        openspec_dir = batch.batch_dir / "openspec"
        for f in ["proposal.md", "design.md", "tasks.md", "spec.md"]:
            path = openspec_dir / f
            if path.exists():
                content = path.read_text(encoding="utf-8")
                print(f"  ✓ {f}: {len(content)} chars")
            else:
                print(f"  ✗ {f}: missing")

        # Check workspace
        workspace = batch.batch_dir / "workspace"
        if workspace.exists():
            py_files = list(workspace.rglob("*.py"))
            print(f"\n  Python files: {len(py_files)}")
            for pf in py_files[:5]:
                print(f"    - {pf.relative_to(workspace)}")

        close_database()

        print_section("Result")
        if batch.status == "completed":
            print("  ✓ E2E test passed!")
            return 0
        else:
            print(f"  ✗ E2E test failed: {batch.error}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
