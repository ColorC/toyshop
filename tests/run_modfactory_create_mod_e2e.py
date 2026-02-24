#!/usr/bin/env python3
"""E2E test: ModFactory → ToyShop bridge (create-mod).

Tests the full pipeline: decompose → ref-scan → decide → enrich → spec → tdd
via the ModFactory bridge.

Requires:
    TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_modfactory_create_mod_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure both toyshop and modfactory are importable
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "modfactory" / "sdk"))

LIVE = os.environ.get("TOYSHOP_RUN_LIVE_E2E", "") == "1"


def main() -> int:
    if not LIVE:
        print("Skipping E2E (set TOYSHOP_RUN_LIVE_E2E=1 to run)")
        return 0

    from toyshop.llm import create_llm, probe_llm

    # Step 0: Check LLM
    print("=" * 60)
    print("ModFactory Create-Mod E2E Test")
    print("=" * 60)

    llm = create_llm()
    ok, err = probe_llm(llm)
    if not ok:
        print(f"SKIP: LLM not available: {err}")
        return 0

    # Step 1: Import bridge
    print("\n--- Step 1: Import bridge ---")
    try:
        from modfactory.toyshop_bridge import create_mod, ModRequest, ModResult
        print("  Bridge imported successfully")
    except ImportError as e:
        print(f"SKIP: ModFactory bridge not available: {e}")
        return 0

    # Step 2: Create mod request
    print("\n--- Step 2: Create mod request ---")
    pm_root = Path("/tmp/toyshop_modfactory_e2e")
    if pm_root.exists():
        import shutil
        shutil.rmtree(pm_root)

    mods_dir = pm_root / "mods"
    mods_dir.mkdir(parents=True)

    ref_config = Path("/home/dministrator/work/modfactory/references.toml")

    request = ModRequest(
        requirement="创建一个简单的寒冰弓mod，发射冰霜投射物",
        mod_name="frost-bow-e2e",
        reference_config=ref_config if ref_config.exists() else None,
        projects_dir=mods_dir,
        force_create=True,
        pm_root=pm_root / "projects",
    )
    print(f"  Requirement: {request.requirement}")
    print(f"  Mod name: {request.mod_name}")
    print(f"  Ref config: {request.reference_config}")

    # Step 3: Run create_mod
    print("\n--- Step 3: Run create_mod pipeline ---")
    result = create_mod(request, llm=llm)

    # Step 4: Verify results
    print("\n--- Step 4: Verify results ---")
    print(f"  Success: {result.success}")
    print(f"  Action: {result.action}")
    print(f"  Error: {result.error}")
    if result.batch_dir:
        print(f"  Batch: {result.batch_dir}")
    if result.mod_path:
        print(f"  Mod path: {result.mod_path}")

    # Check artifacts
    if result.batch_dir:
        batch_dir = result.batch_dir

        # Decomposition
        decomp_path = batch_dir / "decomposition.json"
        if decomp_path.exists():
            decomp = json.loads(decomp_path.read_text(encoding="utf-8"))
            print(f"  Decomposition: {len(decomp.get('aspects', []))} aspects")
        else:
            print("  WARNING: No decomposition.json")

        # Decision
        decision_path = batch_dir / "decision.json"
        if decision_path.exists():
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            print(f"  Decision: {decision.get('action', '?')}")
        else:
            print("  WARNING: No decision.json")

        # Enriched requirement
        enriched_path = batch_dir / "enriched_requirement.md"
        if enriched_path.exists():
            enriched = enriched_path.read_text(encoding="utf-8")
            print(f"  Enriched: {len(enriched)} chars")
        else:
            print("  WARNING: No enriched_requirement.md")

        # OpenSpec docs
        openspec_dir = batch_dir / "openspec"
        if openspec_dir.is_dir():
            docs = list(openspec_dir.glob("*.md"))
            print(f"  OpenSpec docs: {[d.name for d in docs]}")
        else:
            print("  WARNING: No openspec/ directory")

        # Workspace
        workspace = batch_dir / "workspace"
        if workspace.is_dir():
            files = list(workspace.rglob("*"))
            print(f"  Workspace files: {len(files)}")
        else:
            print("  WARNING: No workspace/ directory")

    # Summary
    print("\n" + "=" * 60)
    if result.success:
        print("ModFactory Create-Mod E2E: PASSED")
    else:
        print(f"ModFactory Create-Mod E2E: FAILED — {result.error}")
    print("=" * 60)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
