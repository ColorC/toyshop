"""Reference source configuration and code scanner.

Manages per-project reference source configs (TOML) and provides
grep-based and analyzer-based code scanning for finding SOTA patterns.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ReferenceSource:
    """A single reference source (local codebase or analyzer-backed)."""

    id: str
    name: str
    source_type: str  # "logic" | "mechanism"
    path: str
    language: str
    tags: list[str] = field(default_factory=list)
    description: str = ""
    analyzer: str | None = None  # "modfactory" for ModAnalysis-backed search


@dataclass
class ReferenceConfig:
    """Per-project reference source configuration."""

    project_name: str
    project_type: str
    sources: list[ReferenceSource] = field(default_factory=list)


@dataclass
class CodeSnippet:
    """A code snippet extracted from a reference source."""

    source_id: str
    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str


@dataclass
class ScanResult:
    """Result of scanning a single source for a single aspect."""

    aspect_id: str
    source_id: str
    snippets: list[CodeSnippet] = field(default_factory=list)
    relevance_score: float = 0.0
    relevance_reason: str = ""


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_reference_config(path: Path) -> ReferenceConfig:
    """Load reference config from a TOML file.

    Returns empty config if file doesn't exist.
    """
    if not path.is_file():
        logger.warning("Reference config not found: %s", path)
        return ReferenceConfig(project_name="", project_type="", sources=[])

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)

    sources = []
    for s in data.get("sources", []):
        sources.append(ReferenceSource(
            id=s["id"],
            name=s.get("name", s["id"]),
            source_type=s.get("source_type", "logic"),
            path=s.get("path", ""),
            language=s.get("language", ""),
            tags=s.get("tags", []),
            description=s.get("description", ""),
            analyzer=s.get("analyzer"),
        ))

    return ReferenceConfig(
        project_name=data.get("project_name", ""),
        project_type=data.get("project_type", ""),
        sources=sources,
    )


def save_reference_config(config: ReferenceConfig, path: Path) -> None:
    """Save reference config to a TOML file."""
    lines = [
        f'project_name = "{config.project_name}"',
        f'project_type = "{config.project_type}"',
        "",
    ]
    for s in config.sources:
        lines.append("[[sources]]")
        lines.append(f'id = "{s.id}"')
        lines.append(f'name = "{s.name}"')
        lines.append(f'source_type = "{s.source_type}"')
        lines.append(f'path = "{s.path}"')
        lines.append(f'language = "{s.language}"')
        if s.tags:
            tags_str = ", ".join(f'"{t}"' for t in s.tags)
            lines.append(f"tags = [{tags_str}]")
        if s.description:
            lines.append(f'description = "{s.description}"')
        if s.analyzer:
            lines.append(f'analyzer = "{s.analyzer}"')
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Grep-based scanning
# ---------------------------------------------------------------------------

# File extensions by language for grep filtering
_LANG_EXTENSIONS: dict[str, list[str]] = {
    "python": ["py"],
    "java": ["java"],
    "c++": ["cpp", "cc", "cxx", "h", "hpp"],
    "c": ["c", "h"],
    "c#": ["cs"],
    "typescript": ["ts", "tsx"],
    "javascript": ["js", "jsx"],
    "lua": ["lua"],
    "json": ["json"],
}

_CONTEXT_LINES = 5  # lines before/after match to include


def scan_source_grep(
    source: ReferenceSource,
    keywords: list[str],
    *,
    max_snippets: int = 10,
) -> list[CodeSnippet]:
    """Search a local source directory for keyword matches using grep.

    Returns code snippets with surrounding context.
    """
    source_path = Path(source.path)
    if not source_path.is_dir():
        logger.warning("Source path not found: %s", source_path)
        return []

    # Build grep include patterns
    exts = _LANG_EXTENSIONS.get(source.language, [])
    include_args: list[str] = []
    for ext in exts:
        include_args.extend(["--include", f"*.{ext}"])

    snippets: list[CodeSnippet] = []
    seen_files: set[str] = set()

    for keyword in keywords:
        if len(snippets) >= max_snippets:
            break
        try:
            cmd = [
                "grep", "-rn", "-i",
                *include_args,
                "--", keyword, str(source_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if len(snippets) >= max_snippets:
                    break
                match = re.match(r"^(.+?):(\d+):", line)
                if not match:
                    continue
                file_path = match.group(1)
                line_num = int(match.group(2))

                # Deduplicate by file (one snippet per file per keyword)
                rel_path = str(Path(file_path).relative_to(source_path))
                dedup_key = f"{rel_path}:{keyword}"
                if dedup_key in seen_files:
                    continue
                seen_files.add(dedup_key)

                # Extract context
                content = _extract_context(file_path, line_num, _CONTEXT_LINES)
                if content:
                    start = max(1, line_num - _CONTEXT_LINES)
                    end = line_num + _CONTEXT_LINES
                    snippets.append(CodeSnippet(
                        source_id=source.id,
                        file_path=rel_path,
                        start_line=start,
                        end_line=end,
                        content=content,
                        language=source.language,
                    ))
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("Grep failed for keyword %r in %s: %s", keyword, source.id, e)

    return snippets


def _extract_context(file_path: str, line_num: int, context: int) -> str:
    """Read lines around a match from a file."""
    try:
        path = Path(file_path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, line_num - 1 - context)
        end = min(len(lines), line_num + context)
        return "\n".join(lines[start:end])
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Analyzer-based scanning (for modfactory-analyzed mods)
# ---------------------------------------------------------------------------


def scan_source_analyzer(
    source: ReferenceSource,
    keywords: list[str],
    *,
    max_snippets: int = 10,
    search_limit: int = 3,
) -> list[CodeSnippet]:
    """Search modfactory-analyzed mods for relevant code.

    On-demand flow:
    1. Search Modrinth for mods matching keywords
    2. Download + analyze via analyze_sync() (cached if available)
    3. Search analysis results for matching registries/mixins/classes
    4. Extract decompiled source snippets
    """
    import asyncio

    try:
        from modfactory.mod_repo import ModRepository
        from modfactory.mod_source import Loader
        from modfactory.analyzer import analyze_sync
    except ImportError:
        logger.warning("modfactory not available, skipping analyzer scan")
        return []

    snippets: list[CodeSnippet] = []
    repo = ModRepository()

    # Build a simpler search query from keywords
    # Extract just the meaningful words (skip camelCase, long phrases)
    import re as _re
    search_words = []
    for kw in keywords[:5]:
        # Extract alphabetic words from camelCase and spaces
        words = _re.findall(r'[A-Z]?[a-z]+', kw)
        for w in words:
            if len(w) >= 3 and w.lower() not in ("the", "and", "for", "with"):
                search_words.append(w.lower())
    # Use just the first 2 unique words for a broader search
    seen = set()
    unique_words = []
    for w in search_words:
        if w not in seen:
            seen.add(w)
            unique_words.append(w)
    query = " ".join(unique_words[:2]) if unique_words else "minecraft"

    try:
        search_results = asyncio.run(repo.search(
            query, loader=Loader.FABRIC, mc_version="1.21.1", limit=search_limit,
        ))
    except Exception as e:
        logger.warning("Modrinth search failed: %s", e)
        return []

    for mod_info in search_results:
        if len(snippets) >= max_snippets:
            break
        slug = mod_info.slug  # ModInfo dataclass
        if not slug:
            continue

        # Analyze mod (downloads + decompiles if not cached)
        try:
            analysis = analyze_sync(slug, mc_version="1.21.1")
        except Exception as e:
            logger.warning("Failed to analyze mod %s: %s", slug, e)
            continue

        _collect_snippets_from_analysis(analysis, slug, keywords, snippets, max_snippets)

    return snippets[:max_snippets]


def _collect_snippets_from_analysis(
    analysis: Any,
    slug: str,
    keywords: list[str],
    snippets: list[CodeSnippet],
    max_snippets: int,
) -> None:
    """Extract matching code snippets from a ModAnalysis."""
    # Split keywords into individual words for more lenient matching
    # e.g. "BowItem" -> ["bow", "item"], "custom bow" -> ["custom", "bow"]
    kw_words: list[str] = []
    for kw in keywords:
        # Split on whitespace and camelCase
        import re as _re
        words = _re.findall(r'[A-Z]?[a-z]+', kw)
        kw_words.extend(w.lower() for w in words if len(w) >= 3)

    # Also include original keywords as-is for exact match
    kw_lower = [kw.lower() for kw in keywords] + kw_words

    def matches(text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in kw_lower if kw)

    # Search registries
    for entry in getattr(analysis, "registries", []):
        if len(snippets) >= max_snippets:
            return
        entry_id = getattr(entry, "identifier", "")
        entry_class = getattr(entry, "class_name", "")
        reg_type = getattr(entry, "registry_type", "")
        searchable = f"{entry_id} {entry_class} {reg_type}"
        if matches(searchable):
            src = _read_decompiled_source(analysis, entry_class)
            if src:
                snippets.append(CodeSnippet(
                    source_id=f"mod:{slug}",
                    file_path=f"{entry_class}.java",
                    start_line=1,
                    end_line=src.count("\n") + 1,
                    content=src[:2000],
                    language="java",
                ))

    # Search mixins
    for mixin in getattr(analysis, "mixins", []):
        if len(snippets) >= max_snippets:
            return
        mixin_class = getattr(mixin, "mixin_class", "")
        target_classes = getattr(mixin, "target_classes", [])
        searchable = f"{mixin_class} {' '.join(target_classes)}"
        if matches(searchable):
            src = _read_decompiled_source(analysis, mixin_class)
            if src:
                snippets.append(CodeSnippet(
                    source_id=f"mod:{slug}",
                    file_path=f"{mixin_class}.java (mixin)",
                    start_line=1,
                    end_line=src.count("\n") + 1,
                    content=src[:2000],
                    language="java",
                ))

    # Search classes by superclass/interfaces (useful for finding Entity subclasses etc.)
    for cls in getattr(analysis, "classes", []):
        if len(snippets) >= max_snippets:
            return
        cls_name = getattr(cls, "name", "")
        superclass = getattr(cls, "superclass", "") or ""
        interfaces = getattr(cls, "interfaces", [])
        searchable = f"{cls_name} {superclass} {' '.join(interfaces)}"
        if matches(searchable):
            src = _read_decompiled_source(analysis, cls_name)
            if src:
                snippets.append(CodeSnippet(
                    source_id=f"mod:{slug}",
                    file_path=f"{cls_name}.java",
                    start_line=1,
                    end_line=src.count("\n") + 1,
                    content=src[:2000],
                    language="java",
                ))


def _read_decompiled_source(analysis: Any, class_name: str) -> str:
    """Try to read decompiled source for a class from analysis cache."""
    source_dir = getattr(analysis, "source_dir", None)
    if not source_dir:
        return ""
    # Convert class name to file path: com.example.Foo -> com/example/Foo.java
    rel_path = class_name.replace(".", "/") + ".java"
    full_path = Path(source_dir) / rel_path
    if full_path.is_file():
        try:
            return full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return ""


# ---------------------------------------------------------------------------
# LLM-based relevance scoring
# ---------------------------------------------------------------------------


_SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "reason"],
            },
        },
    },
    "required": ["scores"],
}


def score_snippets(
    aspect: str,
    snippets: list[CodeSnippet],
    llm: Any,
) -> list[tuple[CodeSnippet, float, str]]:
    """Score snippets for relevance to an aspect using LLM.

    Returns (snippet, score, reason) tuples sorted by score descending.
    """
    if not snippets:
        return []

    from toyshop.llm import chat_with_tool

    # Build snippet summaries for LLM
    snippet_texts = []
    for i, s in enumerate(snippets[:20]):  # cap at 20 to avoid huge prompts
        preview = s.content[:500]
        snippet_texts.append(f"[{i}] {s.source_id} / {s.file_path}:\n```\n{preview}\n```")

    system = (
        "You are evaluating code snippets for relevance to a software requirement aspect. "
        "Score each snippet from 0.0 (irrelevant) to 1.0 (highly relevant). "
        "Consider: does this code demonstrate patterns, algorithms, or API usage "
        "that would help implement the given aspect?"
    )
    user = (
        f"Aspect: {aspect}\n\n"
        f"Snippets:\n{''.join(snippet_texts)}\n\n"
        "Score each snippet."
    )

    result = chat_with_tool(
        llm, system, user,
        "score_snippets",
        "Score code snippets for relevance.",
        _SCORE_SCHEMA,
    )

    scored: list[tuple[CodeSnippet, float, str]] = []
    if result and "scores" in result:
        for entry in result["scores"]:
            idx = entry.get("index", -1)
            if 0 <= idx < len(snippets):
                scored.append((
                    snippets[idx],
                    entry.get("score", 0.0),
                    entry.get("reason", ""),
                ))
    else:
        # Fallback: return all with score 0.5
        scored = [(s, 0.5, "LLM scoring unavailable") for s in snippets]

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Main scan entry point
# ---------------------------------------------------------------------------


def scan_references(
    aspect_id: str,
    aspect_type: str,
    keywords: list[str],
    config: ReferenceConfig,
    llm: Any,
    *,
    max_results: int = 5,
) -> list[ScanResult]:
    """Scan reference sources for code relevant to an aspect.

    Picks sources by type (logic/mechanism), scans, scores, returns top results.
    """
    # Filter sources by aspect type or tag overlap with keywords
    kw_lower = [kw.lower() for kw in keywords]
    matching_sources = [
        s for s in config.sources
        if s.source_type == aspect_type
        or any(tag in kw for tag in s.tags for kw in kw_lower)
    ]
    if not matching_sources:
        matching_sources = config.sources  # fallback: search all

    results: list[ScanResult] = []

    for source in matching_sources:
        # Choose scan method
        if source.analyzer == "modfactory":
            snippets = scan_source_analyzer(source, keywords)
        else:
            snippets = scan_source_grep(source, keywords)

        if not snippets:
            continue

        # Score with LLM
        scored = score_snippets(
            f"{aspect_id}: {' '.join(keywords)}", snippets, llm,
        )

        # Take top snippets
        top = scored[:max_results]
        if top:
            avg_score = sum(s for _, s, _ in top) / len(top)
            results.append(ScanResult(
                aspect_id=aspect_id,
                source_id=source.id,
                snippets=[s for s, _, _ in top],
                relevance_score=avg_score,
                relevance_reason=top[0][2] if top else "",
            ))

    results.sort(key=lambda r: r.relevance_score, reverse=True)
    return results[:max_results]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def scan_result_to_dict(result: ScanResult) -> dict:
    """Serialize a ScanResult to a JSON-compatible dict."""
    return {
        "aspect_id": result.aspect_id,
        "source_id": result.source_id,
        "relevance_score": result.relevance_score,
        "relevance_reason": result.relevance_reason,
        "snippets": [asdict(s) for s in result.snippets],
    }


def scan_result_from_dict(data: dict) -> ScanResult:
    """Deserialize a ScanResult from a dict."""
    return ScanResult(
        aspect_id=data["aspect_id"],
        source_id=data["source_id"],
        relevance_score=data.get("relevance_score", 0.0),
        relevance_reason=data.get("relevance_reason", ""),
        snippets=[
            CodeSnippet(**s) for s in data.get("snippets", [])
        ],
    )
