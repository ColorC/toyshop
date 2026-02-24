#!/usr/bin/env python3
"""Scan reference sources for each aspect in a decomposition.

Usage:
    python3 -m toyshop.scripts.ref_scan --decomposition decomposition.json --config refs.toml -o reports/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Scan reference sources for aspects")
    parser.add_argument("--decomposition", "-d", required=True, help="Decomposition JSON")
    parser.add_argument("--config", "-c", required=True, help="Reference config TOML")
    parser.add_argument("--output", "-o", default="reference_reports", help="Output directory")
    parser.add_argument("--max-results", type=int, default=5, help="Max results per aspect")
    args = parser.parse_args()

    # Load decomposition
    from toyshop.decomposer import decomposition_from_dict
    decomp = decomposition_from_dict(
        json.loads(Path(args.decomposition).read_text(encoding="utf-8"))
    )

    # Load reference config
    from toyshop.reference import load_reference_config, scan_references, scan_result_to_dict
    config = load_reference_config(Path(args.config))
    if not config.sources:
        print("Warning: no reference sources configured", file=sys.stderr)

    # Create LLM
    from toyshop.llm import create_llm, probe_llm
    llm = create_llm()
    ok, err = probe_llm(llm)
    if not ok:
        print(f"LLM not available: {err}", file=sys.stderr)
        return 1

    # Scan each aspect
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    for aspect in decomp.aspects:
        print(f"Scanning [{aspect.id}] {aspect.title} ({aspect.aspect_type})...")
        results = scan_references(
            aspect.id, aspect.aspect_type, aspect.keywords,
            config, llm, max_results=args.max_results,
        )
        # Save results
        out_file = out_dir / f"{aspect.id}.json"
        out_file.write_text(
            json.dumps(
                [scan_result_to_dict(r) for r in results],
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        total_snippets = sum(len(r.snippets) for r in results)
        print(f"  → {len(results)} sources, {total_snippets} snippets → {out_file}")

    print(f"\nAll reports saved to {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
