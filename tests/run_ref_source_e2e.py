#!/usr/bin/env python3
"""E2E test: Reference source system pipeline.

Tests the full decompose → ref-scan → decide → enrich flow
with real reference sources (widelands, wesnoth, etc.).

Requires:
    TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_ref_source_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure toyshop is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

LIVE = os.environ.get("TOYSHOP_RUN_LIVE_E2E", "") == "1"


def main() -> int:
    if not LIVE:
        print("Skipping E2E (set TOYSHOP_RUN_LIVE_E2E=1 to run)")
        return 0

    from toyshop.llm import create_llm, probe_llm
    from toyshop.pm import (
        create_batch, run_decompose, run_ref_scan, run_decide, run_enrich,
    )

    # Step 0: Check LLM
    print("=" * 60)
    print("Reference Source E2E Test")
    print("=" * 60)

    llm = create_llm()
    ok, err = probe_llm(llm)
    if not ok:
        print(f"SKIP: LLM not available: {err}")
        return 0

    # Step 1: Create batch
    print("\n--- Step 1: Create batch ---")
    pm_root = Path("/tmp/toyshop_ref_e2e")
    if pm_root.exists():
        import shutil
        shutil.rmtree(pm_root)

    requirement = "创建一个寒冰弓mod，发射冰霜投射物，命中后对目标施加缓慢效果，并在投射物轨迹上产生冰霜粒子效果"
    batch = create_batch(pm_root, "frost-bow-e2e", requirement, project_type="java-minecraft")
    assert batch.batch_dir.exists()
    print(f"  Batch: {batch.batch_dir}")

    # Step 2: Decompose
    print("\n--- Step 2: Decompose requirement ---")
    decomp = run_decompose(batch, llm)
    assert len(decomp.aspects) >= 1, f"Expected at least 1 aspect, got {len(decomp.aspects)}"
    print(f"  Aspects: {len(decomp.aspects)}")
    for a in decomp.aspects:
        print(f"    [{a.id}] {a.title} ({a.aspect_type}/{a.category}) — {a.priority}")
        print(f"      Keywords: {a.keywords}")

    # Verify decomposition.json
    decomp_path = batch.batch_dir / "decomposition.json"
    assert decomp_path.exists()
    decomp_data = json.loads(decomp_path.read_text(encoding="utf-8"))
    assert len(decomp_data["aspects"]) == len(decomp.aspects)

    # Step 3: Reference scan
    print("\n--- Step 3: Scan reference sources ---")
    ref_config = Path(__file__).parent.parent / "references.toml"
    if not ref_config.exists():
        # Try modfactory config
        ref_config = Path("/home/dministrator/work/modfactory/references.toml")

    if ref_config.exists():
        reports = run_ref_scan(batch, llm, ref_config_path=ref_config, max_results=3)
        print(f"  Scanned {len(reports)} aspects")
        total_snippets = 0
        for aspect_id, report_list in reports.items():
            n_snippets = sum(len(r.get("snippets", [])) for r in report_list)
            total_snippets += n_snippets
            print(f"    {aspect_id}: {len(report_list)} sources, {n_snippets} snippets")

        # Verify reports directory
        reports_dir = batch.batch_dir / "reference_reports"
        assert reports_dir.is_dir()
        report_files = list(reports_dir.glob("*.json"))
        assert len(report_files) >= 1, f"Expected at least 1 report file, got {len(report_files)}"
        print(f"  Total snippets: {total_snippets}")
    else:
        print("  SKIP: No reference config found")

    # Step 4: Decide
    print("\n--- Step 4: Decide create vs modify ---")
    mods_dir = Path("/home/dministrator/work/modfactory/mods")
    projects_dir = mods_dir if mods_dir.is_dir() else None
    decision = run_decide(batch, llm, projects_dir=projects_dir)
    print(f"  Action: {decision.action}")
    if decision.target:
        print(f"  Target: {decision.target}")
    print(f"  Rationale: {decision.rationale}")

    # Verify decision.json
    decision_path = batch.batch_dir / "decision.json"
    assert decision_path.exists()

    # Step 5: Enrich
    print("\n--- Step 5: Build enriched requirement ---")
    enriched = run_enrich(batch)
    assert len(enriched) > 100, f"Enriched requirement too short: {len(enriched)} chars"
    print(f"  Enriched: {len(enriched)} chars")

    # Verify enriched_requirement.md
    enriched_path = batch.batch_dir / "enriched_requirement.md"
    assert enriched_path.exists()

    # Verify content
    assert "Enriched Requirement" in enriched
    assert "Aspects" in enriched

    # Check if reference code was included (if we had ref scan)
    if ref_config.exists() and total_snippets > 0:
        assert "Reference Code" in enriched, "Enriched requirement should include reference code"
        print("  Reference code included in enriched requirement")

    # Summary
    print("\n" + "=" * 60)
    print("Reference Source E2E: PASSED")
    print(f"  Aspects: {len(decomp.aspects)}")
    print(f"  Decision: {decision.action}")
    print(f"  Enriched: {len(enriched)} chars")
    print(f"  Batch: {batch.batch_dir}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
