"""LLM-driven intelligent bootstrap for existing codebases.

Replaces the dumb AST-only scanning with a 4-phase flow:
1. Iterative exploration — LLM picks tools to understand the codebase
2. AST snapshot — ground truth for file paths, signatures, imports
3. Document synthesis — LLM generates openspec docs (proposal, design, tasks, spec)
4. Wiki integration — calls bootstrap_from_openspec() to enter the wiki

The output is identical in format to the greenfield pipeline, so bootstrapped
projects seamlessly connect to the brownfield change pipeline.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from openhands.sdk import LLM

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ExplorationState:
    """Accumulated state across exploration iterations."""

    project_root: str
    project_name: str
    language: str = "python"

    # Accumulated findings
    findings: list[dict[str, str]] = field(default_factory=list)
    files_read: set[str] = field(default_factory=set)
    dirs_listed: set[str] = field(default_factory=set)

    # Iteration tracking
    iteration: int = 0
    max_iterations: int = 15
    completed: bool = False

    # Final output (filled by complete_exploration)
    architecture_summary: str = ""
    technology_stack: list[str] = field(default_factory=list)
    key_patterns: list[str] = field(default_factory=list)
    module_descriptions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SmartBootstrapResult:
    """Result of the full smart bootstrap."""

    project_id: str
    version_number: int
    openspec_dir: Path
    modules_count: int
    interfaces_count: int
    exploration_iterations: int


# ---------------------------------------------------------------------------
# Exploration tool schema (discriminated union)
# ---------------------------------------------------------------------------

EXPLORATION_TOOL_NAME = "exploration_action"
EXPLORATION_TOOL_DESC = (
    "Choose an exploration action to understand the codebase. "
    "Call complete_exploration when you have enough information."
)
EXPLORATION_TOOL_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "list_directory",
                "read_file",
                "search_code",
                "ast_scan_module",
                "complete_exploration",
            ],
            "description": "Which exploration action to take.",
        },
        "reasoning": {
            "type": "string",
            "description": "Why you chose this action (1-2 sentences).",
        },
        "directory_path": {
            "type": "string",
            "description": "(list_directory) Relative path to list. Use '.' for root.",
        },
        "file_path": {
            "type": "string",
            "description": "(read_file) Relative path to read.",
        },
        "search_pattern": {
            "type": "string",
            "description": "(search_code) Regex pattern to search for.",
        },
        "file_glob": {
            "type": "string",
            "description": "(search_code) File glob filter, default '*.py'.",
        },
        "module_path": {
            "type": "string",
            "description": "(ast_scan_module) Relative path to .py file.",
        },
        "architecture_summary": {
            "type": "string",
            "description": "(complete_exploration) Overall architecture summary.",
        },
        "technology_stack": {
            "type": "array",
            "items": {"type": "string"},
            "description": "(complete_exploration) Technologies/frameworks used.",
        },
        "key_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "(complete_exploration) Design patterns observed.",
        },
        "module_descriptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file_path": {"type": "string"},
                    "responsibilities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "file_path", "responsibilities"],
            },
            "description": "(complete_exploration) Module descriptions with responsibilities.",
        },
    },
    "required": ["action", "reasoning"],
}


# ---------------------------------------------------------------------------
# .toyignore support
# ---------------------------------------------------------------------------

def _load_toyignore(root: Path) -> list[str]:
    """Load ignore patterns from .toyignore file in project root.

    Format: one pattern per line, # for comments, trailing / stripped.
    """
    toyignore = root / ".toyignore"
    if not toyignore.is_file():
        return []
    patterns: list[str] = []
    for line in toyignore.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _should_skip_dir(name: str, ignore_patterns: list[str]) -> bool:
    """Check if a directory name should be skipped."""
    import fnmatch
    if name in _SKIP_DIRS or name.startswith("."):
        return True
    name_clean = name.rstrip("/")
    for pat in ignore_patterns:
        pat_clean = pat.rstrip("/")
        if fnmatch.fnmatch(name_clean, pat_clean):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

_SKIP_DIRS = {"__pycache__", ".git", ".tox", ".mypy_cache", "node_modules",
              ".eggs", "*.egg-info", ".venv", "venv", "env"}


def _safe_resolve(root: Path, rel: str) -> Path | None:
    """Resolve *rel* under *root*, rejecting path escapes."""
    try:
        target = (root / rel).resolve()
        if not str(target).startswith(str(root.resolve())):
            return None
        return target
    except (ValueError, OSError):
        return None


def _exec_list_directory(root: Path, path: str, ignore_patterns: list[str] | None = None) -> str:
    """List directory contents with type indicators."""
    target = _safe_resolve(root, path)
    if target is None or not target.is_dir():
        return f"[error] directory not found or path escape: {path}"
    entries: list[str] = []
    try:
        for item in sorted(target.iterdir()):
            if _should_skip_dir(item.name, ignore_patterns or []):
                continue
            suffix = "/" if item.is_dir() else ""
            entries.append(f"  {item.name}{suffix}")
    except PermissionError:
        return f"[error] permission denied: {path}"
    header = f"Directory: {path} ({len(entries)} entries)"
    return header + "\n" + "\n".join(entries) if entries else header + "\n  (empty)"


def _exec_read_file(root: Path, path: str, max_lines: int = 200) -> str:
    """Read file contents, truncated to max_lines."""
    target = _safe_resolve(root, path)
    if target is None or not target.is_file():
        return f"[error] file not found: {path}"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError) as e:
        return f"[error] cannot read {path}: {e}"
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    content = "\n".join(lines[:max_lines])
    if truncated:
        content += f"\n... ({len(lines) - max_lines} more lines truncated)"
    return f"File: {path} ({len(lines)} lines)\n{content}"


def _exec_search_code(root: Path, pattern: str, file_glob: str = "*.py",
                      ignore_patterns: list[str] | None = None) -> str:
    """Search for pattern in codebase using grep."""
    cmd = ["grep", "-rn", "--include", file_glob, "-E", pattern]
    for pat in (ignore_patterns or []):
        cmd.extend(["--exclude-dir", pat.rstrip("/")])
    # Also exclude default skip dirs
    for d in _SKIP_DIRS:
        cmd.extend(["--exclude-dir", d])
    cmd.append(".")
    try:
        result = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return f"[error] search failed for pattern: {pattern}"
    if not lines:
        return f"No matches for pattern: {pattern}"
    # Limit output
    if len(lines) > 30:
        lines = lines[:30]
        lines.append(f"... (more matches truncated)")
    return f"Search: {pattern} ({len(lines)} matches)\n" + "\n".join(lines)


def _exec_ast_scan_module(root: Path, path: str) -> str:
    """Run AST scan on a single Python file."""
    from toyshop.snapshot import scan_python_file

    target = _safe_resolve(root, path)
    if target is None or not target.is_file():
        return f"[error] file not found: {path}"
    try:
        mod = scan_python_file(target, relative_to=root)
    except Exception as e:
        return f"[error] AST scan failed for {path}: {e}"
    parts = [f"Module: {mod.name} ({mod.file_path}, {mod.line_count} lines)"]
    if mod.imports:
        parts.append(f"Imports: {', '.join(mod.imports[:15])}")
    if mod.classes:
        for cls in mod.classes:
            methods_str = ", ".join(cls.methods[:5])
            if len(cls.methods) > 5:
                methods_str += f" ... (+{len(cls.methods) - 5})"
            bases = f"({', '.join(cls.bases)})" if cls.bases else ""
            parts.append(f"Class: {cls.name}{bases} — methods: [{methods_str}]")
    if mod.functions:
        for fn in mod.functions:
            parts.append(f"Function: {fn.signature}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Exploration loop
# ---------------------------------------------------------------------------

_EXPLORATION_SYSTEM_PROMPT = """\
You are a senior software architect reverse-engineering an existing codebase.
Your goal is to understand the project's architecture deeply enough to produce
accurate documentation.

## Available Actions
- list_directory: List files in a directory
- read_file: Read a file's contents
- search_code: Search for patterns across the codebase
- ast_scan_module: Get AST structure of a Python file (classes, functions, imports)
- complete_exploration: Finish exploration with structured findings

## Strategy (4 phases)

Phase 1 — Project Overview (iterations 1-3):
- List the root directory to understand project layout
- Read configuration files (pyproject.toml, setup.py, README.md, Cargo.toml, etc.)
- Identify the main source package and test directories

Phase 2 — Module Deep-Dive (iterations 4-8):
- Read key source files to understand their purpose
- Use ast_scan_module for structural analysis of important files
- Identify classes, functions, and their relationships

Phase 3 — Dependency & Pattern Analysis (iterations 9-12):
- Search for import patterns to map module dependencies
- Identify design patterns (factory, strategy, observer, etc.)
- Look for configuration, middleware, or plugin patterns

Phase 4 — Summarize & Complete:
- When you have sufficient understanding, call complete_exploration
- Provide module_descriptions with responsibilities and dependencies for EACH
  source module (not test files)
- Include architecture_summary and technology_stack

## Rules
- Do NOT re-read files you've already read
- Do NOT re-list directories you've already listed
- Focus on SOURCE code, not test files (tests will be analyzed separately)
- Skip __pycache__, .git, node_modules, .venv, etc.
- Call complete_exploration when you have enough information OR when running
  low on iterations
"""


def _build_exploration_user_content(state: ExplorationState) -> str:
    """Build user content with current state for each iteration."""
    parts = [
        f"Project: {state.project_name}",
        f"Root: {state.project_root}",
        f"Language: {state.language}",
        f"Iteration: {state.iteration + 1} of {state.max_iterations}",
    ]
    if state.dirs_listed:
        parts.append(f"\nDirectories already listed: {', '.join(sorted(state.dirs_listed))}")
    if state.files_read:
        parts.append(f"\nFiles already read: {', '.join(sorted(state.files_read))}")
    if state.findings:
        recent = state.findings[-10:]
        parts.append("\nRecent findings:")
        for f in recent:
            parts.append(f"  [{f.get('category', '?')}] {f.get('summary', '')}")
    if state.iteration >= state.max_iterations - 2:
        parts.append(
            "\n⚠ Running low on iterations! Call complete_exploration soon "
            "with your best understanding so far."
        )
    return "\n".join(parts)


def _dispatch_tool(root: Path, action: dict[str, Any], state: ExplorationState,
                   ignore_patterns: list[str] | None = None) -> str:
    """Execute a tool action and return the result string."""
    act = action.get("action", "")
    if act == "list_directory":
        path = action.get("directory_path", ".")
        state.dirs_listed.add(path)
        return _exec_list_directory(root, path, ignore_patterns)
    elif act == "read_file":
        path = action.get("file_path", "")
        state.files_read.add(path)
        return _exec_read_file(root, path)
    elif act == "search_code":
        pattern = action.get("search_pattern", "")
        glob = action.get("file_glob", "*.py")
        return _exec_search_code(root, pattern, glob, ignore_patterns)
    elif act == "ast_scan_module":
        path = action.get("module_path", "")
        state.files_read.add(path)
        return _exec_ast_scan_module(root, path)
    else:
        return f"[error] unknown action: {act}"


def run_exploration(llm: "LLM", state: ExplorationState,
                    ignore_patterns: list[str] | None = None) -> ExplorationState:
    """Run the iterative exploration loop."""
    from toyshop.llm import chat_with_tool

    root = Path(state.project_root)

    for i in range(state.max_iterations):
        state.iteration = i
        user_content = _build_exploration_user_content(state)

        result = chat_with_tool(
            llm,
            _EXPLORATION_SYSTEM_PROMPT,
            user_content,
            EXPLORATION_TOOL_NAME,
            EXPLORATION_TOOL_DESC,
            EXPLORATION_TOOL_PARAMS,
        )

        if result is None:
            logger.warning("Exploration iteration %d: no tool call returned", i)
            continue

        action = result.get("action", "")
        reasoning = result.get("reasoning", "")
        logger.info("Exploration [%d/%d] %s: %s", i + 1, state.max_iterations, action, reasoning)

        # Handle complete_exploration
        if action == "complete_exploration":
            state.architecture_summary = result.get("architecture_summary", "")
            state.technology_stack = result.get("technology_stack", [])
            state.key_patterns = result.get("key_patterns", [])
            state.module_descriptions = result.get("module_descriptions", [])
            state.completed = True
            state.iteration = i + 1
            logger.info("Exploration completed at iteration %d", i + 1)
            return state

        # Execute tool and accumulate finding
        tool_output = _dispatch_tool(root, result, state, ignore_patterns)
        state.findings.append({
            "category": action,
            "summary": f"{action}: {reasoning}",
            "detail": tool_output[:500],
            "file_path": result.get("file_path", result.get("directory_path", "")),
        })

    # Force completion at max iterations
    state.iteration = state.max_iterations
    state.completed = True
    logger.warning("Exploration force-completed at max iterations (%d)", state.max_iterations)
    return state


# ---------------------------------------------------------------------------
# Synthesis — generate openspec documents from exploration + AST
# ---------------------------------------------------------------------------

def _exploration_context(state: ExplorationState) -> str:
    """Build a text summary of exploration findings for synthesis prompts."""
    parts = [f"Project: {state.project_name}"]
    if state.architecture_summary:
        parts.append(f"\nArchitecture Summary:\n{state.architecture_summary}")
    if state.technology_stack:
        parts.append(f"\nTechnology Stack: {', '.join(state.technology_stack)}")
    if state.key_patterns:
        parts.append(f"\nDesign Patterns: {', '.join(state.key_patterns)}")
    if state.module_descriptions:
        parts.append("\nModules:")
        for m in state.module_descriptions:
            parts.append(f"  - {m.get('name', '?')} ({m.get('file_path', '?')})")
            for r in m.get("responsibilities", []):
                parts.append(f"    • {r}")
            deps = m.get("dependencies", [])
            if deps:
                parts.append(f"    deps: {', '.join(deps)}")
    return "\n".join(parts)


def _snapshot_context(snapshot: Any) -> str:
    """Build a text summary of AST snapshot for synthesis prompts."""
    parts = [f"AST Snapshot: {len(snapshot.modules)} modules"]
    for mod in snapshot.modules:
        classes = [c.name for c in mod.classes]
        funcs = [f.name for f in mod.functions]
        parts.append(f"  {mod.file_path}: classes={classes}, functions={funcs}")
    return "\n".join(parts)


def _synthesize_proposal(
    llm: "LLM",
    exploration: ExplorationState,
    snapshot: Any,
) -> str:
    """Generate proposal.md via LLM tool call + openspec renderer."""
    from toyshop.llm import chat_with_tool
    from toyshop.openspec.generator import generate_proposal, render_proposal_markdown
    from toyshop.openspec.types import ProposalInput

    system = (
        "You are documenting an existing codebase. Generate a project proposal "
        "based on the exploration findings and AST snapshot below. "
        "This is a BOOTSTRAP of an existing project, not a new project proposal."
    )
    user = (
        f"{_exploration_context(exploration)}\n\n"
        f"{_snapshot_context(snapshot)}\n\n"
        "Generate the proposal using the tool."
    )

    # Use the ProposalInput schema
    schema = ProposalInput.model_json_schema()

    result = chat_with_tool(
        llm, system, user,
        "generate_proposal",
        "Generate a project proposal document.",
        schema,
    )

    if result is None:
        raise RuntimeError("LLM returned no tool call for proposal synthesis")
    try:
        inp = ProposalInput.model_validate(result)
        proposal = generate_proposal(inp)
        return render_proposal_markdown(proposal)
    except Exception as e:
        raise RuntimeError(f"Proposal synthesis failed: {e}") from e


def _build_modules_from_ast(
    snapshot: Any,
    exploration: ExplorationState,
) -> "list[ModuleDefinition]":
    """Build ModuleDefinition list from AST snapshot + exploration descriptions."""
    from toyshop.openspec.types import ModuleDefinition

    # Build lookup from exploration module_descriptions
    desc_map: dict[str, dict] = {}
    for md in exploration.module_descriptions:
        name = md.get("name", "")
        if name:
            desc_map[name] = md
            # Also index by file_path stem
            fp = md.get("file_path", "")
            if fp:
                stem = fp.rsplit("/", 1)[-1].replace(".py", "")
                desc_map[stem] = md

    modules = []
    for mod in snapshot.modules:
        if _is_test_file(mod.file_path):
            continue
        # Module ID: use file path stem, replace / with _
        mod_id = mod.file_path.replace(".py", "").replace("/", "_").replace("__init__", "init")
        # Look up exploration description
        exp = desc_map.get(mod.name, {})
        responsibilities = exp.get("responsibilities", [])
        if not responsibilities:
            # Derive from AST: list classes and functions
            parts = []
            if mod.classes:
                parts.append(f"Defines: {', '.join(c.name for c in mod.classes)}")
            if mod.functions:
                parts.append(f"Functions: {', '.join(f.name for f in mod.functions)}")
            responsibilities = parts or ["Module implementation"]
        dependencies = exp.get("dependencies", [])
        description = exp.get("description", f"Module {mod.name}")

        modules.append(ModuleDefinition(
            id=mod_id,
            name=mod.name,
            description=description,
            responsibilities=responsibilities,
            dependencies=dependencies,
            filePath=mod.file_path,
        ))
    return modules


def _build_interfaces_from_ast(
    snapshot: Any,
    modules: "list[ModuleDefinition]",
) -> "list[InterfaceDefinition]":
    """Build InterfaceDefinition list from AST snapshot."""
    from toyshop.openspec.types import InterfaceDefinition, InterfaceType

    # Build module_id lookup by file_path
    path_to_id = {}
    for m in modules:
        path_to_id[m.file_path] = m.id

    interfaces = []
    for mod in snapshot.modules:
        if _is_test_file(mod.file_path):
            continue
        module_id = path_to_id.get(mod.file_path, mod.name)

        # Classes
        for cls in mod.classes:
            intf_id = f"{module_id}_{cls.name}"
            bases = f"({', '.join(cls.bases)})" if cls.bases else ""
            interfaces.append(InterfaceDefinition(
                id=intf_id,
                name=cls.name,
                type=InterfaceType.CLASS,
                signature=f"class {cls.name}{bases}",
                description=f"Class in {mod.name}",
                moduleId=module_id,
            ))

        # Top-level functions (skip private helpers)
        for func in mod.functions:
            if func.name.startswith("_"):
                continue
            intf_id = f"{module_id}_{func.name}"
            interfaces.append(InterfaceDefinition(
                id=intf_id,
                name=func.name,
                type=InterfaceType.FUNCTION,
                signature=func.signature,
                description=f"Function in {mod.name}",
                moduleId=module_id,
            ))
    return interfaces


# Schema for LLM design enrichment (high-level only, no modules/interfaces)
_DESIGN_ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement": {
            "type": "string",
            "description": "High-level requirement / purpose of the project.",
        },
        "architecture_summary": {
            "type": "string",
            "description": "2-3 paragraph summary of the overall architecture.",
        },
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "metrics": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "description"],
            },
            "description": "Architecture goals.",
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "context": {"type": "string"},
                    "decision": {"type": "string"},
                    "consequences": {"type": "string"},
                },
                "required": ["id", "title", "context", "decision", "consequences"],
            },
            "description": "Key architecture decisions (ADRs).",
        },
        "module_descriptions": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Map of module name → description (1-2 sentences each).",
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "mitigation": {"type": "string"},
                },
                "required": ["description", "severity", "mitigation"],
            },
        },
        "tradeoffs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "aspect": {"type": "string"},
                    "choice": {"type": "string"},
                    "alternative": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["aspect", "choice", "alternative", "rationale"],
            },
        },
    },
    "required": ["requirement", "goals", "decisions", "module_descriptions"],
}


def _synthesize_design(
    llm: "LLM",
    exploration: ExplorationState,
    snapshot: Any,
) -> str:
    """Generate design.md: AST-driven modules/interfaces + LLM enrichment.

    Strategy: modules and interfaces come from the AST snapshot (ground truth).
    The LLM provides high-level architecture understanding: requirement, goals,
    decisions, risks, tradeoffs, and module descriptions.

    This ensures the design.md is always parseable by _parse_design_modules()
    and _parse_design_interfaces(), regardless of LLM output quality.
    """
    from toyshop.llm import chat_with_tool
    from toyshop.openspec.generator import render_design_markdown
    from toyshop.openspec.types import (
        ArchitectureDecision, Goal, InterfaceType, ModuleDefinition,
        OpenSpecDesign, Risk, Tradeoff,
    )

    # Phase 1: Build modules and interfaces from AST (deterministic)
    modules = _build_modules_from_ast(snapshot, exploration)
    interfaces = _build_interfaces_from_ast(snapshot, modules)
    logger.info("AST-built design: %d modules, %d interfaces", len(modules), len(interfaces))

    # Phase 2: Ask LLM for high-level enrichment
    system = (
        "You are documenting an existing codebase's architecture. "
        "The modules and interfaces are already extracted from the AST. "
        "Your job is to provide HIGH-LEVEL understanding:\n"
        "- requirement: what this project does\n"
        "- goals: architecture goals\n"
        "- decisions: key architecture decisions (ADRs)\n"
        "- module_descriptions: a map of module name → description\n"
        "- risks and tradeoffs\n\n"
        "Base your analysis on the exploration findings below."
    )
    # Summarize modules for the LLM (just names + file paths)
    mod_list = "\n".join(f"  - {m.name} ({m.file_path})" for m in modules)
    user = (
        f"{_exploration_context(exploration)}\n\n"
        f"Modules ({len(modules)} source modules):\n{mod_list}\n\n"
        "Generate the high-level design enrichment using the tool."
    )

    result = chat_with_tool(
        llm, system, user,
        "generate_design",
        "Generate high-level architecture analysis for the project.",
        _DESIGN_ENRICHMENT_SCHEMA,
    )

    # Phase 3: Merge LLM enrichment into AST-built design
    requirement = "Project architecture documentation"
    goals: list[Goal] = []
    decisions: list[ArchitectureDecision] = []
    risks: list[Risk] = []
    tradeoffs: list[Tradeoff] = []

    if result:
        requirement = result.get("requirement", requirement)
        for g in result.get("goals", []):
            try:
                goals.append(Goal(**g))
            except Exception:
                pass
        for d in result.get("decisions", []):
            try:
                decisions.append(ArchitectureDecision(**d))
            except Exception:
                pass
        # Enrich module descriptions
        desc_map = result.get("module_descriptions", {})
        for mod in modules:
            if mod.name in desc_map:
                mod.description = desc_map[mod.name]
        for r in result.get("risks", []):
            try:
                risks.append(Risk(**r))
            except Exception:
                pass
        for t in result.get("tradeoffs", []):
            try:
                tradeoffs.append(Tradeoff(**t))
            except Exception:
                pass

    design = OpenSpecDesign(
        requirement=requirement,
        goals=goals,
        decisions=decisions,
        modules=modules,
        interfaces=interfaces,
        risks=risks,
        tradeoffs=tradeoffs,
    )
    return render_design_markdown(design)


def _synthesize_tasks(
    llm: "LLM",
    exploration: ExplorationState,
    design_md: str,
) -> str:
    """Generate tasks.md — all tasks marked completed for bootstrap."""
    from toyshop.llm import chat_with_tool
    from toyshop.openspec.generator import generate_tasks, render_tasks_markdown
    from toyshop.openspec.types import TasksInput

    system = (
        "You are documenting the implementation tasks of an existing codebase. "
        "Since this is a BOOTSTRAP (code already exists), ALL tasks should be "
        "marked as 'completed'. Generate tasks that represent the logical "
        "implementation steps that were already done."
    )
    user = (
        f"{_exploration_context(exploration)}\n\n"
        f"Design document:\n{design_md[:3000]}\n\n"
        "Generate tasks using the tool. Mark all as completed."
    )

    schema = TasksInput.model_json_schema()

    result = chat_with_tool(
        llm, system, user,
        "generate_tasks",
        "Generate a task list for the project.",
        schema,
    )

    if result is None:
        raise RuntimeError("LLM returned no tool call for tasks synthesis")
    try:
        inp = TasksInput.model_validate(result)
        tasks = generate_tasks(inp)
        return render_tasks_markdown(tasks)
    except Exception as e:
        raise RuntimeError(f"Tasks synthesis failed: {e}") from e


def _synthesize_spec(
    llm: "LLM",
    exploration: ExplorationState,
    snapshot: Any,
) -> str:
    """Generate spec.md with GIVEN/WHEN/THEN scenarios."""
    from toyshop.llm import chat_with_tool
    from toyshop.openspec.generator import generate_spec, render_spec_markdown
    from toyshop.openspec.types import SpecInput

    system = (
        "You are documenting the behavioral specification of an existing codebase. "
        "Generate Gherkin-style scenarios (GIVEN/WHEN/THEN) that describe the "
        "key behaviors of the system based on the exploration findings."
    )
    user = (
        f"{_exploration_context(exploration)}\n\n"
        f"{_snapshot_context(snapshot)}\n\n"
        "Generate specification scenarios using the tool."
    )

    schema = SpecInput.model_json_schema()

    result = chat_with_tool(
        llm, system, user,
        "generate_spec",
        "Generate behavioral specification scenarios.",
        schema,
    )

    if result is None:
        raise RuntimeError("LLM returned no tool call for spec synthesis")
    try:
        inp = SpecInput.model_validate(result)
        spec = generate_spec(inp)
        return render_spec_markdown(spec)
    except Exception as e:
        raise RuntimeError(f"Spec synthesis failed: {e}") from e


def _is_test_file(file_path: str) -> bool:
    """Check if a file path looks like a test file."""
    parts = Path(file_path).parts
    name = Path(file_path).name
    return (
        any(p in ("tests", "test", "testing") for p in parts)
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def smart_bootstrap(
    project_name: str,
    workspace: Path,
    *,
    llm: "LLM",
    project_type: str = "python",
    language: str = "python",
    db_path: Path | None = None,
    max_iterations: int = 15,
) -> SmartBootstrapResult:
    """Intelligent bootstrap: explore → AST snapshot → synthesize openspec → wiki.

    Args:
        project_name: Name for the project in the wiki
        workspace: Root directory of the codebase
        llm: LLM instance (required)
        project_type: Project type identifier
        language: Primary language
        db_path: Database path (default: workspace/.toyshop/architecture.db)
        max_iterations: Max exploration iterations

    Returns:
        SmartBootstrapResult with project_id, version info, and stats
    """
    from toyshop.snapshot import create_snapshot
    from toyshop.storage.database import init_database
    from toyshop.storage.wiki import bootstrap_from_openspec

    workspace = workspace.resolve()

    # Load .toyignore
    ignore_patterns = _load_toyignore(workspace)
    if ignore_patterns:
        logger.info("Loaded .toyignore: %s", ignore_patterns)

    # Initialize DB
    if db_path is None:
        db_path = workspace / ".toyshop" / "architecture.db"
    init_database(db_path)

    # Phase 1: Exploration
    state = ExplorationState(
        project_root=str(workspace),
        project_name=project_name,
        language=language,
        max_iterations=max_iterations,
    )

    logger.info("Starting LLM-driven exploration of %s", workspace)
    state = run_exploration(llm, state, ignore_patterns)
    logger.info(
        "Exploration done: %d iterations, %d findings, completed=%s",
        state.iteration, len(state.findings), state.completed,
    )

    # Phase 2: AST snapshot (ground truth)
    logger.info("Running AST snapshot...")
    snapshot = create_snapshot(workspace, project_name, ignore_patterns=ignore_patterns)
    # Filter out test files from snapshot for architecture docs
    source_modules = [m for m in snapshot.modules if not _is_test_file(m.file_path)]
    logger.info("AST snapshot: %d total modules, %d source modules",
                len(snapshot.modules), len(source_modules))

    # Phase 3: Synthesis
    with tempfile.TemporaryDirectory() as tmpdir:
        openspec_dir = Path(tmpdir) / "openspec"
        openspec_dir.mkdir()

        logger.info("Synthesizing openspec documents with LLM...")
        proposal_md = _synthesize_proposal(llm, state, snapshot)
        design_md = _synthesize_design(llm, state, snapshot)
        tasks_md = _synthesize_tasks(llm, state, design_md)
        spec_md = _synthesize_spec(llm, state, snapshot)

        # Write openspec files
        (openspec_dir / "proposal.md").write_text(proposal_md, encoding="utf-8")
        (openspec_dir / "design.md").write_text(design_md, encoding="utf-8")
        (openspec_dir / "tasks.md").write_text(tasks_md, encoding="utf-8")
        (openspec_dir / "spec.md").write_text(spec_md, encoding="utf-8")

        logger.info("Openspec docs written to %s", openspec_dir)

        # Phase 4: Wiki integration
        project_id, version = bootstrap_from_openspec(
            project_name=project_name,
            workspace=workspace,
            openspec_dir=openspec_dir,
            project_type=project_type,
            language=language,
        )

        # Count modules/interfaces from the design
        from toyshop.tdd_pipeline import _parse_design_modules, _parse_design_interfaces
        modules_count = len(_parse_design_modules(design_md))
        interfaces_count = len(_parse_design_interfaces(design_md))

        return SmartBootstrapResult(
            project_id=project_id,
            version_number=version.version_number,
            openspec_dir=openspec_dir,
            modules_count=modules_count,
            interfaces_count=interfaces_count,
            exploration_iterations=state.iteration,
        )
