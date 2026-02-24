#!/usr/bin/env python3
"""Decompose a requirement into typed aspects.

Usage:
    python3 -m toyshop.scripts.decompose --input "requirement text" --type java-minecraft -o decomposition.json
    python3 -m toyshop.scripts.decompose --input-file req.md --type python -o decomposition.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Decompose requirement into aspects")
    parser.add_argument("--input", "-i", help="Requirement text")
    parser.add_argument("--input-file", help="Read requirement from file")
    parser.add_argument("--type", "-t", default="python", help="Project type (python, java-minecraft, ...)")
    parser.add_argument("--context", help="Optional context text or file")
    parser.add_argument("--output", "-o", default="decomposition.json", help="Output JSON path")
    args = parser.parse_args()

    # Read requirement
    if args.input_file:
        requirement = Path(args.input_file).read_text(encoding="utf-8")
    elif args.input:
        requirement = args.input
    else:
        print("Error: provide --input or --input-file", file=sys.stderr)
        return 1

    context = ""
    if args.context:
        p = Path(args.context)
        context = p.read_text(encoding="utf-8") if p.is_file() else args.context

    # Create LLM
    from toyshop.llm import create_llm, probe_llm
    llm = create_llm()
    ok, err = probe_llm(llm)
    if not ok:
        print(f"LLM not available: {err}", file=sys.stderr)
        return 1

    # Decompose
    from toyshop.decomposer import decompose_requirement, decomposition_to_dict
    result = decompose_requirement(requirement, args.type, llm, context=context)

    # Write output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(decomposition_to_dict(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Decomposed into {len(result.aspects)} aspects:")
    for a in result.aspects:
        print(f"  [{a.id}] {a.title} ({a.aspect_type}/{a.category}) — {a.priority}")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
