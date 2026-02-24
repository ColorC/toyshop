#!/usr/bin/env python3
"""Smart bootstrap E2E: ToyShop bootstraps itself with real LLM.

Tests the full 4-phase intelligent bootstrap pipeline:
1. LLM-driven exploration of ToyShop's own codebase
2. AST snapshot for ground truth
3. LLM synthesis of openspec documents
4. Wiki integration

Usage:
    TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_smart_bootstrap_e2e.py [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from toyshop.storage.database import init_database, close_database
from toyshop.storage.wiki import get_latest_version, list_versions, get_project_summary
from toyshop.self_host import bootstrap_self, generate_self_change_request


def check_env():
    if not os.environ.get("TOYSHOP_RUN_LIVE_E2E"):
        print("Skipping: set TOYSHOP_RUN_LIVE_E2E=1 to run live E2E")
        return False
    return True


def save_docs(output_dir: Path, version, stats: dict):
    output_dir.mkdir(parents=True, exist_ok=True)
    for attr, fname in [
        ("proposal_md", "proposal.md"),
        ("design_md", "design.md"),
        ("tasks_md", "tasks.md"),
        ("spec_md", "spec.md"),
    ]:
        content = getattr(version, attr, None)
        if content:
            (output_dir / fname).write_text(content, encoding="utf-8")
    import json
    (output_dir / "summary.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )

def main():
    if not check_env():
        return 0

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/toyshop_smart_bootstrap_review")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    print("=" * 60)
    print(" ToyShop Smart Bootstrap E2E")
    print("=" * 60)

    # Step 1: Create LLM and probe
    print("\n[Step 1] Creating LLM and probing availability...")
    from toyshop.llm import create_llm, probe_llm
    import logging
    logging.basicConfig(level=logging.INFO, format="  %(name)s: %(message)s")

    llm = create_llm()
    ok, err = probe_llm(llm)
    if not ok:
        print(f"  ✗ LLM not available: {err}")
        return 1
    print(f"  ✓ LLM available: {llm.model}")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "toyshop_smart.db"

        # Step 2: Smart bootstrap
        print("\n[Step 2] Running smart bootstrap (this may take 2-5 minutes)...")
        project_id = bootstrap_self(db_path=db_path, smart=True, llm=llm)
        print(f"  ✓ Project ID: {project_id}")

        # Step 3: Retrieve frozen openspec
        print("\n[Step 3] Retrieving frozen openspec from wiki...")
        version = get_latest_version(project_id)
        if not version:
            print("  ✗ No version found!")
            return 1
        print(f"  ✓ Version: v{version.version_number}")

        docs = {
            "proposal": version.proposal_md,
            "design": version.design_md,
            "tasks": version.tasks_md,
            "spec": version.spec_md,
        }
        for name, content in docs.items():
            status = f"{len(content)} chars" if content else "MISSING"
            print(f"  ✓ {name}.md: {status}")

        # Step 4: Save docs for review (before assertions so we can inspect on failure)
        print(f"\n[Step 4] Saving docs to {output_dir}...")
        from toyshop.tdd_pipeline import _parse_design_modules, _parse_design_interfaces
        modules = _parse_design_modules(version.design_md)
        interfaces = _parse_design_interfaces(version.design_md)
        stats = {
            "project_id": project_id,
            "version": version.version_number,
            "modules_count": len(modules),
            "interfaces_count": len(interfaces),
            "doc_sizes": {k: len(v) if v else 0 for k, v in docs.items()},
        }
        save_docs(output_dir, version, stats)
        print(f"  ✓ Saved to {output_dir}")

        # Step 5: Validate design.md
        print("\n[Step 5] Validating design.md parseability...")
        print(f"  ✓ Modules parsed: {len(modules)}")
        print(f"  ✓ Interfaces parsed: {len(interfaces)}")

        # Check key modules present
        module_names = " ".join(m.get("name", "") for m in modules).lower()
        key_terms = ["smart_bootstrap", "tdd_pipeline", "llm", "wiki", "snapshot", "openspec"]
        hits = [t for t in key_terms if t in module_names]
        print(f"  ✓ Key modules found: {hits}")

        assert len(modules) >= 10, f"Expected ≥10 modules, got {len(modules)}"
        assert len(interfaces) >= 5, f"Expected ≥5 interfaces, got {len(interfaces)}"
        assert len(hits) >= 3, f"Expected ≥3 key modules, found {hits}"

        # Step 6: Generate change request
        print("\n[Step 6] Generating self-change request...")
        change = generate_self_change_request(
            project_id=project_id,
            description="Add Java AST scanning support to smart_bootstrap for multi-language projects",
        )
        print(f"  ✓ Change plan ID: {change['change_plan_id']}")
        print(f"  ✓ Status: {change['status']}")

        # Step 7: Summary
        print("\n" + "=" * 60)
        print(f" ✓ Smart bootstrap E2E passed!")
        print(f"   Modules: {len(modules)}, Interfaces: {len(interfaces)}")
        print(f"   Review docs at: {output_dir}")
        print("=" * 60)

        close_database()
        return 0


if __name__ == "__main__":
    sys.exit(main())