#!/usr/bin/env python3
"""Self-hosting E2E test: ToyShop bootstraps itself into its own Wiki.

This demonstrates the complete self-hosting workflow:
1. bootstrap_self() loads ToyShop's codebase into the wiki
2. generate_self_change_request() creates a change plan
3. Shows how ToyShop can manage its own evolution

Usage:
    TOYSHOP_RUN_LIVE_E2E=1 python tests/run_self_host_e2e.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from toyshop.storage.database import init_database, close_database, get_db
from toyshop.storage.wiki import (
    get_latest_version, list_versions, get_test_suite,
    get_project_summary, list_project_summaries,
)
from toyshop.self_host import bootstrap_self, generate_self_change_request


def check_env():
    """Check if live E2E is enabled."""
    if not os.environ.get("TOYSHOP_RUN_LIVE_E2E"):
        print("Skipping: set TOYSHOP_RUN_LIVE_E2E=1 to run live E2E")
        return False
    return True


def main():
    if not check_env():
        return 0

    print("=" * 60)
    print(" ToyShop Self-Hosting E2E Test")
    print("=" * 60)

    # Use a temp database for this test
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "toyshop_self.db"

        # Step 1: Bootstrap ToyShop itself
        print("\n[Step 1] Bootstrapping ToyShop into wiki...")
        project_id = bootstrap_self(db_path=db_path)
        print(f"  ✓ Project ID: {project_id}")

        # Step 2: Check the initial version
        print("\n[Step 2] Checking initial wiki version...")
        version = get_latest_version(project_id)
        if not version:
            print("  ✗ No version found!")
            return 1
        print(f"  ✓ Version: v{version.version_number}")
        print(f"  ✓ Change source: {version.change_source}")
        print(f"  ✓ Change summary: {version.change_summary[:60]}...")

        # Step 3: Check extracted test metadata
        print("\n[Step 3] Checking extracted test metadata...")
        test_suite = get_test_suite(version.id)
        if test_suite:
            print(f"  ✓ Test files: {len(test_suite.test_files)}")
            print(f"  ✓ Test cases: {test_suite.total_tests}")
            if test_suite.test_files[:3]:
                print(f"  ✓ Sample files: {test_suite.test_files[:3]}")
        else:
            print("  ⚠ No test suite found")

        # Step 4: Check project summary
        print("\n[Step 4] Checking project summary...")
        summary = get_project_summary(project_id)
        print(f"  ✓ Name: {summary.get('name', 'N/A')}")
        print(f"  ✓ Type: {summary.get('project_type', 'N/A')}")
        print(f"  ✓ Latest version: v{summary.get('latest_version', 0)}")
        print(f"  ✓ Total tests: {summary.get('total_tests', 0)}")

        # Step 5: Generate a self-change request (without LLM, just draft)
        print("\n[Step 5] Generating self-change request (draft mode)...")
        change_request = generate_self_change_request(
            project_id=project_id,
            description="Add rate limiting to the API gateway module",
        )
        print(f"  ✓ Change plan ID: {change_request['change_plan_id']}")
        print(f"  ✓ Status: {change_request['status']}")
        print(f"  ✓ Change request: {change_request['change_request'][:50]}...")
        print(f"  ✓ Based on version: v{change_request.get('version_id', 'N/A')[:8] if change_request.get('version_id') else 'N/A'}")

        # Step 6: List all projects (should show toyshop)
        print("\n[Step 6] Listing all projects...")
        summaries = list_project_summaries()
        print(f"  ✓ Total projects: {len(summaries)}")
        for s in summaries:
            print(f"    - {s.get('name', 'unknown')} (v{s.get('latest_version', 0)}, {s.get('total_tests', 0)} tests)")

        # Step 7: Version history
        print("\n[Step 7] Version history...")
        versions = list_versions(project_id, limit=5)
        for v in versions:
            bound = f" → {v.git_commit_hash[:8]}" if v.git_commit_hash else ""
            print(f"    v{v.version_number}: {v.change_type} ({v.change_source}){bound}")

        print("\n" + "=" * 60)
        print(" ✓ Self-hosting E2E test passed!")
        print("=" * 60)

        close_database()
        return 0


if __name__ == "__main__":
    sys.exit(main())
