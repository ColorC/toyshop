#!/usr/bin/env python3
"""Decide whether to create a new project or modify an existing one.

Usage:
    python3 -m toyshop.scripts.decide --decomposition decomposition.json --projects-dir mods/ -o decision.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Decide create vs modify")
    parser.add_argument("--decomposition", "-d", required=True, help="Decomposition JSON")
    parser.add_argument("--projects-dir", "-p", required=True, help="Directory of existing projects")
    parser.add_argument("--type", "-t", default="", help="Project type filter")
    parser.add_argument("--output", "-o", default="decision.json", help="Output JSON path")
    args = parser.parse_args()

    # Load decomposition
    from toyshop.decomposer import decomposition_from_dict
    decomp = decomposition_from_dict(
        json.loads(Path(args.decomposition).read_text(encoding="utf-8"))
    )

    # Analyze existing projects
    from toyshop.decision_engine import (
        analyze_existing_projects, decide_create_or_modify, decision_to_dict,
    )
    candidates = analyze_existing_projects(Path(args.projects_dir), args.type)
    print(f"Found {len(candidates)} existing projects:")
    for c in candidates:
        print(f"  - {c.name}")

    # Create LLM
    from toyshop.llm import create_llm, probe_llm
    llm = create_llm()
    ok, err = probe_llm(llm)
    if not ok:
        print(f"LLM not available: {err}", file=sys.stderr)
        return 1

    # Decide
    decision = decide_create_or_modify(decomp, candidates, llm)

    # Write output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(decision_to_dict(decision), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nDecision: {decision.action}")
    if decision.target:
        print(f"Target: {decision.target}")
    print(f"Rationale: {decision.rationale}")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
