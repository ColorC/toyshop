#!/usr/bin/env python3
"""ModFactory smart bootstrap E2E: multi-language Python+Java project.

Bootstraps the ModFactory project and saves generated openspec docs for review.
Does NOT continue development — purely for quality assessment.

Usage:
    TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_modfactory_bootstrap_e2e.py [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from toyshop.storage.database import init_database, close_database
from toyshop.storage.wiki import get_latest_version

MODFACTORY_ROOT = Path("/home/dministrator/work/modfactory")


def check_env():
    if not os.environ.get("TOYSHOP_RUN_LIVE_E2E"):
        print("Skipping: set TOYSHOP_RUN_LIVE_E2E=1 to run live E2E")
        return False
    if not MODFACTORY_ROOT.is_dir():
        print(f"Skipping: {MODFACTORY_ROOT} not found")
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
    (output_dir / "summary.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )

def main():
    if not check_env():
        return 0

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/modfactory_bootstrap_review")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    print("=" * 60)
    print(" ModFactory Smart Bootstrap E2E")
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

    # Step 2: Check .toyignore
    toyignore = MODFACTORY_ROOT / ".toyignore"
    if toyignore.exists():
        print(f"\n[Step 2] .toyignore found:")
        for line in toyignore.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                print(f"  - {line.strip()}")
    else:
        print("\n[Step 2] No .toyignore found (scanning everything)")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "modfactory.db"

        # Step 3: Smart bootstrap
        print("\n[Step 3] Running smart bootstrap (this may take 3-8 minutes)...")
        from toyshop.smart_bootstrap import smart_bootstrap
        result = smart_bootstrap(
            project_name="modfactory",
            workspace=MODFACTORY_ROOT,
            llm=llm,
            project_type="python",
            language="python",
            db_path=db_path,
            max_iterations=15,
        )
        print(f"  ✓ Project ID: {result.project_id}")
        print(f"  ✓ Exploration iterations: {result.exploration_iterations}")
        print(f"  ✓ Modules: {result.modules_count}")
        print(f"  ✓ Interfaces: {result.interfaces_count}")

        # Step 4: Retrieve frozen openspec
        print("\n[Step 4] Retrieving frozen openspec from wiki...")
        version = get_latest_version(result.project_id)
        if not version:
            print("  ✗ No version found!")
            return 1

        docs = {
            "proposal": version.proposal_md,
            "design": version.design_md,
            "tasks": version.tasks_md,
            "spec": version.spec_md,
        }
        for name, content in docs.items():
            status = f"{len(content)} chars" if content else "MISSING"
            print(f"  ✓ {name}.md: {status}")

        # Step 5: Save docs for review (before assertions so we can inspect on failure)
        print(f"\n[Step 5] Saving docs to {output_dir}...")
        from toyshop.tdd_pipeline import _parse_design_modules, _parse_design_interfaces

        modules = _parse_design_modules(version.design_md)
        interfaces = _parse_design_interfaces(version.design_md)

        # Check Python SDK modules
        module_text = version.design_md.lower()
        sdk_terms = ["rcon", "server_manager", "mc_agent", "visual", "analyzer",
                      "config", "e2e_test", "mod_repo"]
        sdk_hits = [t for t in sdk_terms if t in module_text]

        stats = {
            "project_id": result.project_id,
            "version": result.version_number,
            "exploration_iterations": result.exploration_iterations,
            "modules_count": len(modules),
            "interfaces_count": len(interfaces),
            "sdk_terms_found": sdk_hits,
            "doc_sizes": {k: len(v) if v else 0 for k, v in docs.items()},
            "module_names": [m.get("name", "?") for m in modules],
        }
        save_docs(output_dir, version, stats)
        print(f"  ✓ Saved to {output_dir}")

        # Step 6: Validate design.md
        print("\n[Step 6] Validating design.md...")
        print(f"  ✓ Modules parsed: {len(modules)}")
        print(f"  ✓ Interfaces parsed: {len(interfaces)}")
        print(f"  ✓ Python SDK terms found: {sdk_hits}")

        assert len(modules) >= 5, f"Expected ≥5 modules, got {len(modules)}"
        assert len(interfaces) >= 3, f"Expected ≥3 interfaces, got {len(interfaces)}"
        assert len(sdk_hits) >= 1, f"Expected ≥1 SDK term, found none"

        # Step 7: Summary
        print("\n" + "=" * 60)
        print(f" ✓ ModFactory bootstrap E2E passed!")
        print(f"   Modules: {len(modules)}, Interfaces: {len(interfaces)}")
        print(f"   Review docs at: {output_dir}")
        print("=" * 60)

        close_database()
        return 0


if __name__ == "__main__":
    sys.exit(main())