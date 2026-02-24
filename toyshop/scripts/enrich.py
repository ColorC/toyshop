#!/usr/bin/env python3
"""Build enriched requirement from decomposition + references + decision.

Usage:
    python3 -m toyshop.scripts.enrich \
        --decomposition decomposition.json \
        --refs reference_reports/ \
        --decision decision.json \
        -o enriched_requirement.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def build_enriched_requirement(
    decomp_data: dict,
    ref_reports: dict[str, list[dict]],
    decision_data: dict | None,
) -> str:
    """Build a markdown requirement enriched with reference code snippets."""
    sections = []

    # Original requirement
    sections.append("# Enriched Requirement\n")
    sections.append(f"## Original Requirement\n\n{decomp_data['original_requirement']}\n")

    # Decision context
    if decision_data:
        action = decision_data.get("action", "create")
        sections.append(f"## Decision: {action.upper()}\n")
        if decision_data.get("target"):
            sections.append(f"Target project: {decision_data['target']}\n")
        sections.append(f"Rationale: {decision_data.get('rationale', '')}\n")

    # Aspects with references
    sections.append("## Aspects\n")
    for aspect in decomp_data.get("aspects", []):
        aid = aspect["id"]
        sections.append(f"### [{aid}] {aspect['title']}")
        sections.append(f"- Type: {aspect['aspect_type']}")
        sections.append(f"- Category: {aspect.get('category', 'general')}")
        sections.append(f"- Priority: {aspect.get('priority', 'must')}")
        sections.append(f"- Description: {aspect['description']}\n")

        # Reference snippets for this aspect
        reports = ref_reports.get(aid, [])
        if reports:
            sections.append("#### Reference Code\n")
            for report in reports:
                source_id = report.get("source_id", "?")
                score = report.get("relevance_score", 0)
                reason = report.get("relevance_reason", "")
                sections.append(f"**{source_id}** (relevance: {score:.1f})")
                if reason:
                    sections.append(f"> {reason}\n")
                for snippet in report.get("snippets", [])[:3]:
                    lang = snippet.get("language", "")
                    fp = snippet.get("file_path", "")
                    sections.append(f"`{fp}`:")
                    sections.append(f"```{lang}")
                    sections.append(snippet.get("content", "")[:1000])
                    sections.append("```\n")
        sections.append("")

    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(description="Build enriched requirement")
    parser.add_argument("--decomposition", "-d", required=True, help="Decomposition JSON")
    parser.add_argument("--refs", "-r", help="Reference reports directory")
    parser.add_argument("--decision", help="Decision JSON")
    parser.add_argument("--output", "-o", default="enriched_requirement.md", help="Output markdown")
    args = parser.parse_args()

    # Load decomposition
    decomp_data = json.loads(Path(args.decomposition).read_text(encoding="utf-8"))

    # Load reference reports
    ref_reports: dict[str, list[dict]] = {}
    if args.refs:
        refs_dir = Path(args.refs)
        if refs_dir.is_dir():
            for f in refs_dir.glob("*.json"):
                aspect_id = f.stem
                ref_reports[aspect_id] = json.loads(f.read_text(encoding="utf-8"))

    # Load decision
    decision_data = None
    if args.decision:
        dp = Path(args.decision)
        if dp.is_file():
            decision_data = json.loads(dp.read_text(encoding="utf-8"))

    # Build enriched requirement
    enriched = build_enriched_requirement(decomp_data, ref_reports, decision_data)

    # Write output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(enriched, encoding="utf-8")

    print(f"Enriched requirement: {len(enriched)} chars → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
