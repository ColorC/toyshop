"""TDD Pipeline - Test-Driven Development with permission-isolated agents.

Pipeline flow:
  Phase 1: Signature Extraction (pure Python, no agent)
  Phase 2: Test Generation (Test Agent - write mode, restricted to tests/)
  Phase 3: Code Implementation (Code Agent - blocked from tests/)
  Phase 3.5: Test Fix (Test Agent - fix mode, if code agent hit boundary violation)
  Phase 4: White-box Verification (Test Agent - verify mode, read-only)
  Phase 4.5: Debug Analysis (Debug Agent - probes, fault localization, hypotheses)
  Phase 5: Black-box Verification (auto-generated from spec.md scenarios)
  Phase 6: Final Report (legacy issues, debug history)

Key design: agents have different tool permissions enforced at the executor level,
not just via prompt instructions. Debug Agent uses hypothesis-driven debugging with
diagnostic probes and SBFL fault localization.

Cross-boundary violations: when an agent tries to edit files outside its allowed
directories, the violation is detected and the request is re-routed to the
appropriate agent (e.g., code agent -> test fix agent for test bugs).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, TYPE_CHECKING

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.tool import (
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)

# Import tools to trigger registration
import openhands.tools.terminal  # noqa: F401
import openhands.tools.glob  # noqa: F401
import openhands.tools.grep  # noqa: F401

from openhands.tools.file_editor.definition import (
    TOOL_DESCRIPTION,
    FileEditorAction,
    FileEditorObservation,
    FileEditorTool,
)
from openhands.tools.file_editor.impl import FileEditorExecutor

# Reuse FileReadTool from ux_agent
from toyshop.ux_agent import FileReadTool

# Debug subsystems
from toyshop.rollback import RollbackManager
from toyshop.debug_probe import get_instrumentor, reset_probe_counter
from toyshop.fault_localize import FaultLocalizer, SuspiciousLine
from toyshop.debug_hypothesis import (
    DebugHypothesis,
    DebugReport,
    CodingChallenge,
    ProbeEvidence,
    get_hypothesis_manager,
    parse_challenge_from_finish,
    # v2 Debug Form system
    DebugForm,
    DebugFormSet,
    Rejection,
    parse_rejection_from_finish,
    get_debug_form_executor,
    reset_debug_form_executor,
)
from toyshop.test_combination import (
    expose_as_whitebox,
    generate_variant_tests_for_failures,
)
from toyshop.expected_comparison import (
    TestVerdict,
    LegacyIssue,
    mark_as_legacy,
)

# Import tools to trigger registration of new tools
import toyshop.debug_probe  # noqa: F401 — registers probe_tool
import toyshop.fault_localize  # noqa: F401 — registers fault_localize
import toyshop.debug_hypothesis  # noqa: F401 — registers hypothesis_tool

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


# =============================================================================
# Constants
# =============================================================================

MAX_WHITEBOX_RETRIES = 3
MAX_BLACKBOX_RETRIES = 2
MAX_TOTAL_RETRIES = 5
MAX_CHALLENGE_RETRIES = 2  # max times Coding Agent can challenge hypotheses

# v2 Debug Form flow constants
MAX_DEBUG_FORM_RETRIES = 3   # max outer debug loop iterations
MAX_REJECTION_RETRIES = 2    # max Code Agent → Test Agent rejection ping-pongs


# =============================================================================
# Result types
# =============================================================================

@dataclass
class PerTestResult:
    """Individual test result from automated test run."""
    test_id: str           # e.g. "tests/test_calc.py::test_add"
    status: str            # "passed" | "failed" | "error" | "skipped"
    failure_message: str = ""


@dataclass
class TestRunResult:
    """Parsed pytest output."""
    all_passed: bool
    total: int
    passed: int
    failed: int
    errors: int
    output: str
    per_test: list[PerTestResult] = field(default_factory=list)


@dataclass
class SignatureManifest:
    """Output of signature extraction phase."""
    stub_files: list[str]
    test_dir: str
    modules: list[dict[str, Any]]
    interfaces: list[dict[str, Any]]


@dataclass
class TDDResult:
    """Result of the full TDD pipeline."""
    success: bool
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    stub_files: list[str] = field(default_factory=list)
    whitebox_passed: bool = False
    blackbox_passed: bool = False
    whitebox_output: str = ""
    blackbox_output: str = ""
    summary: str = ""
    retry_count: int = 0
    # Debug enhancement fields
    legacy_issues: list[LegacyIssue] = field(default_factory=list)
    debug_reports: list[DebugReport] = field(default_factory=list)
    verdicts: list[TestVerdict] = field(default_factory=list)


@dataclass
class BoundaryViolation:
    """Detected when an agent tries to write outside its allowed directories."""
    agent_role: str       # "code" or "test"
    target_path: str      # file the agent tried to edit
    agent_reasoning: str  # finish message explaining why


def _detect_boundary_violations(
    conversation: Conversation, agent_role: str,
) -> list[BoundaryViolation]:
    """Scan conversation events for blocked write operations.

    Returns a list of BoundaryViolation if the agent tried to edit files
    outside its allowed directories (detected via FileEditorObservation errors).
    """
    from openhands.sdk.event import ObservationEvent

    violations: list[BoundaryViolation] = []
    seen_paths: set[str] = set()

    finish_msg = _extract_finish_message(conversation)

    for event in conversation.state.events:
        if not isinstance(event, ObservationEvent):
            continue
        # Check for blocked write error messages from DirectoryRestrictedFileEditorExecutor
        text = ""
        if hasattr(event, "observation") and hasattr(event.observation, "content"):
            for item in event.observation.content:
                if hasattr(item, "text"):
                    text += item.text
        if not text:
            continue
        # Match: "Write operation 'xxx' blocked on '/path/to/file'."
        m = re.search(r"Write operation '[^']+' blocked on '([^']+)'", text)
        if m:
            path = m.group(1)
            if path not in seen_paths:
                seen_paths.add(path)
                violations.append(BoundaryViolation(
                    agent_role=agent_role,
                    target_path=path,
                    agent_reasoning=finish_msg[:2000],
                ))

    return violations


# =============================================================================
# Directory-restricted file editor executor
# =============================================================================

class DirectoryRestrictedFileEditorExecutor(ToolExecutor):
    """FileEditorExecutor that restricts write operations by directory.

    Supports two modes:
    - allowed_write_dirs: whitelist — only allow writes under these dirs
    - blocked_write_dirs: blacklist — block writes under these dirs

    The `view` command is always allowed regardless of restrictions.
    """

    def __init__(
        self,
        workspace_root: str | None = None,
        allowed_write_dirs: list[Path] | None = None,
        blocked_write_dirs: list[Path] | None = None,
    ):
        self.inner = FileEditorExecutor(workspace_root=workspace_root)
        self.allowed_write_dirs = (
            [Path(d).resolve() for d in allowed_write_dirs]
            if allowed_write_dirs else None
        )
        self.blocked_write_dirs = (
            [Path(d).resolve() for d in blocked_write_dirs]
            if blocked_write_dirs else None
        )

    @staticmethod
    def _is_under(path: Path, directory: Path) -> bool:
        try:
            path.relative_to(directory)
            return True
        except ValueError:
            return False

    def __call__(
        self,
        action: FileEditorAction,
        conversation: "LocalConversation | None" = None,
    ) -> FileEditorObservation:
        # view is always allowed
        if action.command != "view":
            action_path = Path(action.path).resolve()

            # Whitelist check
            if self.allowed_write_dirs is not None:
                if not any(self._is_under(action_path, d) for d in self.allowed_write_dirs):
                    dirs_str = ", ".join(str(d) for d in self.allowed_write_dirs)
                    return FileEditorObservation.from_text(
                        text=(
                            f"Write operation '{action.command}' blocked on '{action_path}'. "
                            f"Only allowed under: [{dirs_str}]"
                        ),
                        command=action.command,
                        is_error=True,
                    )

            # Blacklist check
            if self.blocked_write_dirs is not None:
                if any(self._is_under(action_path, d) for d in self.blocked_write_dirs):
                    dirs_str = ", ".join(str(d) for d in self.blocked_write_dirs)
                    return FileEditorObservation.from_text(
                        text=(
                            f"Write operation '{action.command}' blocked on '{action_path}'. "
                            f"Writes forbidden under: [{dirs_str}]"
                        ),
                        command=action.command,
                        is_error=True,
                    )

        return self.inner(action, conversation)


# =============================================================================
# Restricted tool factories
# =============================================================================

def _make_test_file_editor(
    conv_state: "ConversationState", **params: Any
) -> Sequence[ToolDefinition]:
    """Factory: file_editor that can only write under tests/."""
    workspace = conv_state.workspace.working_dir
    executor = DirectoryRestrictedFileEditorExecutor(
        workspace_root=workspace,
        allowed_write_dirs=[Path(workspace) / "tests"],
    )
    return [
        FileEditorTool(
            action_type=FileEditorAction,
            observation_type=FileEditorObservation,
            description=(
                TOOL_DESCRIPTION
                + f"\n\nYour working directory is: {workspace}\n"
                + "RESTRICTION: You can only create/edit files under the tests/ directory."
            ),
            executor=executor,
            annotations=ToolAnnotations(
                title="test_file_editor",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        )
    ]


def _make_code_file_editor(
    conv_state: "ConversationState", **params: Any
) -> Sequence[ToolDefinition]:
    """Factory: file_editor that cannot write under tests/."""
    workspace = conv_state.workspace.working_dir
    executor = DirectoryRestrictedFileEditorExecutor(
        workspace_root=workspace,
        blocked_write_dirs=[Path(workspace) / "tests"],
    )
    return [
        FileEditorTool(
            action_type=FileEditorAction,
            observation_type=FileEditorObservation,
            description=(
                TOOL_DESCRIPTION
                + f"\n\nYour working directory is: {workspace}\n"
                + "RESTRICTION: You CANNOT create/edit files under the tests/ directory."
            ),
            executor=executor,
            annotations=ToolAnnotations(
                title="code_file_editor",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        )
    ]


# Register restricted tool variants
register_tool("test_file_editor", _make_test_file_editor)
register_tool("code_file_editor", _make_code_file_editor)


# =============================================================================
# Phase 1: Signature extraction (pure Python, no agent)
# =============================================================================

def _parse_design_interfaces(design_md: str) -> list[dict[str, str]]:
    """Parse interface signatures from design.md markdown.

    Supports multiple formats:
      Format A (hand-written / stress-test style):
        ## Module: mdtable.parser
        ### Class: `Table`
        #### Methods
        `def __init__(self, ...) -> None`
        `def __len__(self) -> int`
        ### Function: `parse`
        `def parse(text: str) -> Table`

      Format B (LLM-generated style):
        #### parse (`parser-3`)
        - **Type:** function
        - **Signature:** `def parse(...) -> Table`
    """
    interfaces: list[dict[str, str]] = []
    lines = design_md.split("\n")
    i = 0
    current_name = ""
    current_module = ""
    current_class = ""
    in_methods_section = False
    while i < len(lines):
        line = lines[i].strip()

        # Format A: ## Module: mdtable.parser
        if line.startswith("## Module:"):
            current_module = line.replace("## Module:", "").strip()

        # Format A: ### Class: `ClassName`
        elif line.startswith("### Class:"):
            cls = line.replace("### Class:", "").strip().strip("`")
            current_class = cls
            current_name = cls
            interfaces.append({"name": cls, "signature": f"class {cls}", "module": current_module})
            in_methods_section = False

        # Format A: ### Function: `func_name`
        elif line.startswith("### Function:"):
            fname = line.replace("### Function:", "").strip().strip("`")
            current_name = fname
            current_class = ""
            in_methods_section = False

        # Format A: #### Methods / #### Attributes
        elif line.startswith("#### Methods"):
            in_methods_section = True

        elif line.startswith("#### Attributes"):
            in_methods_section = False

        # Format B: #### InterfaceName or #### InterfaceName (`module-id`)
        elif line.startswith("#### ") and not line.startswith("#### ADR-"):
            raw = line[5:].strip()
            paren_idx = raw.find(" (")
            if paren_idx > 0:
                current_name = raw[:paren_idx].strip()
                mod_part = raw[paren_idx:].strip().strip("()")
                current_module = mod_part.strip("`").strip()
            else:
                current_name = raw
            in_methods_section = False

        # Bare backtick signature line: `def foo(...)` or `(a: int) -> int`
        elif line.startswith("`") and line.endswith("`") and (current_name or in_methods_section):
            sig = line.strip("`").strip()
            # Extract name from signature
            if sig.startswith("def "):
                name = sig.split("(")[0].replace("def ", "").strip()
            elif sig.startswith("class "):
                name = sig.split("(")[0].split(":")[0].replace("class ", "").strip()
            elif sig.startswith("@"):
                i += 1
                continue  # skip decorators
            elif current_name:
                name = current_name
            else:
                i += 1
                continue
            interfaces.append({"name": name, "signature": sig, "module": current_module})
            # Don't reset current_name in methods section (multiple sigs under one heading)
            if not in_methods_section and not current_class:
                current_name = ""

        # Format B: - **Signature:** `...` (may be single or multi-line)
        elif line.startswith("- **Signature:**") and current_name:
            sig_text = line.replace("- **Signature:**", "").strip()
            if sig_text.startswith("`") and not sig_text.endswith("`"):
                sig_lines = [sig_text.lstrip("`")]
                i += 1
                while i < len(lines):
                    sl = lines[i].rstrip()
                    if sl.rstrip().endswith("`"):
                        sig_lines.append(sl.rstrip().rstrip("`"))
                        break
                    sig_lines.append(sl)
                    i += 1
                sig_text = "\n".join(sig_lines)
            else:
                sig_text = sig_text.strip("`").strip()
            sig_lines_parsed = sig_text.split("\n")
            first_line = sig_lines_parsed[0].strip()
            if first_line.startswith("class "):
                interfaces.append({"name": current_name, "signature": first_line.rstrip(":"), "module": current_module})
                for sl in sig_lines_parsed[1:]:
                    sl = sl.strip()
                    if not sl or sl.startswith("#"):
                        continue
                    if sl.startswith("def "):
                        method_name = sl.split("(")[0].replace("def ", "").strip()
                        interfaces.append({"name": method_name, "signature": sl, "module": current_module})
            else:
                interfaces.append({"name": current_name, "signature": first_line, "module": current_module})
            current_name = ""

        # Format B: - **Module:** `module_name`
        elif line.startswith("- **Module:**") and current_name:
            current_module = line.replace("- **Module:**", "").strip().strip("`").strip()

        i += 1
    return interfaces


def _parse_design_modules(design_md: str) -> list[dict[str, str]]:
    """Parse module info from design.md markdown.

    Supports:
      Format A: ## Module: mdtable.parser
      Format B: ### Modules / #### ModuleName (`id`) / - **File:** `path`
    """
    modules: list[dict[str, str]] = []
    lines = design_md.split("\n")
    in_modules = False
    current: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        # Format A: ## Module: mdtable.parser
        if stripped.startswith("## Module:") and not stripped.startswith("## Modules"):
            if current:
                modules.append(current)
            mod_name = stripped.replace("## Module:", "").strip()
            current = {"name": mod_name, "filePath": ""}
            continue
        # Format B: ### Modules section
        if stripped == "### Modules":
            in_modules = True
            continue
        if in_modules and stripped.startswith("### ") and stripped != "### Modules":
            if current:
                modules.append(current)
            break
        if in_modules and stripped.startswith("#### "):
            if current:
                modules.append(current)
            current = {"name": stripped[5:].strip(), "filePath": ""}
        if (in_modules or current) and (stripped.startswith("- **Path:**") or stripped.startswith("- **File:**")):
            path = stripped.replace("- **Path:**", "").replace("- **File:**", "").strip().strip("`").strip()
            if current:
                current["filePath"] = path
    if current and current not in modules:
        modules.append(current)
    return modules


def _normalize_signature(name: str, sig: str) -> str:
    """Ensure signature is valid Python: prepend 'def name' if missing."""
    sig = sig.strip()
    if sig.startswith("def ") or sig.startswith("class "):
        return sig
    # Bare signature like "(a: float, b: float) -> float"
    if sig.startswith("("):
        return f"def {name}{sig}"
    # Just a type or name without parens
    return f"def {name}({sig})"


def _is_python_signature(sig: str) -> bool:
    """Check if a signature looks like valid Python (not TypeScript/JS)."""
    ts_markers = [
        "extends ", " => ", "readonly ", "keyof ", "Partial<",
        "interface ", "type ", ": string", ": number", ": boolean",
        ": any", "?: ", "string[]", "number[]", "boolean[]",
        "<T", "Record<", "Promise<",
    ]
    sig_lower = sig.lower()
    for marker in ts_markers:
        if marker.lower() in sig_lower:
            return False
    # Must start with def/class/@dataclass or be a bare signature like (a: int) -> int
    stripped = sig.strip()
    if stripped.startswith(("def ", "class ", "@", "(")):
        return True
    # Likely not Python
    return False


def _generate_stub_code(interfaces: list[dict[str, str]]) -> str:
    """Generate Python stub code from parsed interfaces."""
    # Filter out non-Python signatures
    py_interfaces = [i for i in interfaces if _is_python_signature(i["signature"])]

    lines: list[str] = [
        '"""Auto-generated stubs from design.md signatures."""',
        "",
        "from __future__ import annotations",
        "from typing import Any, List, Union",
        "from dataclasses import dataclass",
        "",
    ]

    # Group: separate classes from standalone functions
    classes: dict[str, list[dict[str, str]]] = {}
    functions: list[dict[str, str]] = []

    for iface in py_interfaces:
        sig = _normalize_signature(iface["name"], iface["signature"])
        iface = {**iface, "signature": sig}
        if sig.startswith("class "):
            class_name = sig.replace("class ", "").strip()
            classes[class_name] = []
        elif "self" in sig:
            # Method — find which class it belongs to
            # Heuristic: assign to the most recently defined class
            if classes:
                last_class = list(classes.keys())[-1]
                classes[last_class].append(iface)
            else:
                functions.append(iface)
        else:
            functions.append(iface)

    # Generate class stubs
    for class_name, methods in classes.items():
        lines.append(f"class {class_name}:")
        if not methods:
            lines.append("    pass")
        else:
            for method in methods:
                sig = method["signature"]
                lines.append(f"    {sig}:")
                lines.append(f'        raise NotImplementedError("TODO: implement {method["name"]}")')
                lines.append("")
        lines.append("")

    # Generate standalone function stubs
    for func in functions:
        sig = func["signature"]
        lines.append(f"{sig}:")
        lines.append(f'    raise NotImplementedError("TODO: implement {func["name"]}")')
        lines.append("")

    return "\n".join(lines)


def extract_signatures(workspace: Path, mode: str = "create") -> SignatureManifest:
    """Parse openspec/design.md and generate stub files.

    Args:
        workspace: Project workspace directory
        mode: "create" overwrites stubs; "modify" preserves existing code files

    Returns a SignatureManifest with paths to generated stubs.
    """
    design_path = workspace / "openspec" / "design.md"
    if not design_path.exists():
        return SignatureManifest(stub_files=[], test_dir="tests", modules=[], interfaces=[])

    design_md = design_path.read_text(encoding="utf-8")
    modules = _parse_design_modules(design_md)
    interfaces = _parse_design_interfaces(design_md)

    if not interfaces:
        return SignatureManifest(stub_files=[], test_dir="tests", modules=modules, interfaces=interfaces)

    # Generate stub code
    stub_code = _generate_stub_code(interfaces)

    # Determine output path from modules or use default
    # Find the first module with a filePath, or use a default
    stub_path = None
    for mod in modules:
        fp = mod.get("filePath", "").strip()
        if fp:
            stub_path = workspace / fp
            break

    if stub_path is None:
        # Default: use project name from first module or "project"
        project_name = "project"
        if modules:
            name = modules[0].get("name", "").lower().replace(" ", "_")
            if name:
                project_name = name
        stub_dir = workspace / project_name
        stub_dir.mkdir(parents=True, exist_ok=True)
        stub_path = stub_dir / "stubs.py"

    # Ensure parent directory exists
    stub_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "modify" and stub_path.exists():
        # In modify mode, preserve existing code — don't overwrite with stubs.
        # The existing file has real implementations that we want to keep.
        # Just record it as the stub file for reference.
        pass
    else:
        stub_path.write_text(stub_code, encoding="utf-8")

    # Create __init__.py if needed
    init_path = stub_path.parent / "__init__.py"
    if not init_path.exists():
        init_path.write_text("", encoding="utf-8")

    # Create tests directory
    test_dir = workspace / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)

    stub_files = [str(stub_path.relative_to(workspace))]

    return SignatureManifest(
        stub_files=stub_files,
        test_dir="tests",
        modules=modules,
        interfaces=interfaces,
    )


# =============================================================================
# Agent prompts
# =============================================================================

TEST_AGENT_WRITE_PROMPT = """You are a test engineer. Write comprehensive pytest tests based on design documents and code stubs.

## Your Workflow
1. Read openspec/design.md and openspec/spec.md to understand requirements and interfaces
2. Read the stub files to understand function/class signatures
3. Write pytest test files in the tests/ directory
4. Include: unit tests for each interface, edge case tests, integration tests
5. Tests should import from the stub modules and test the public API

## Rules
- ONLY create/edit files under the tests/ directory
- Do NOT implement any production code
- Write tests that will initially FAIL (stubs raise NotImplementedError)
- Use descriptive test names: test_<feature>_<scenario>
- Include both happy-path and error-handling tests
- Use pytest fixtures where appropriate
- If you believe IMPLEMENTATION CODE has a bug, do NOT try to edit it.
  Instead, explain in your finish message what is wrong and why.
  The system will route your request to the coding agent for fixing.

When done, call finish with a summary of test files created.
"""

# --- Modify-mode prompt variants ---

TEST_AGENT_WRITE_MODIFY_PROMPT = """You are a test engineer. Write additional pytest tests for CHANGED and NEW interfaces only.

## Context
This is a MODIFY operation on an existing codebase. Existing tests already cover unchanged interfaces.
You must preserve all existing test files and only ADD new tests for the changed parts.

## Your Workflow
1. Read openspec/design.md and openspec/spec.md to understand the CHANGES
2. Read existing test files in tests/ to understand what's already covered
3. Read the stub/code files to understand new/changed function signatures
4. Write NEW test files for changed interfaces (e.g. tests/test_<feature>_change.py)
5. Do NOT modify existing test files unless they import changed signatures

## Rules
- ONLY create/edit files under the tests/ directory
- Do NOT implement any production code
- PRESERVE all existing test files — do not delete or rewrite them
- Focus on testing NEW and CHANGED interfaces only
- Use descriptive test names: test_<feature>_<scenario>
- Include both happy-path and error-handling tests for new functionality
- If you believe IMPLEMENTATION CODE has a bug, do NOT try to edit it.
  Instead, explain in your finish message what is wrong and why.

When done, call finish with a summary of test files created/modified.
"""

TDD_CODE_AGENT_MODIFY_PROMPT = """You are an expert developer. Modify existing code to make all tests pass.

## Context
This is a MODIFY operation on an existing codebase. You must make targeted edits to existing files.
Do NOT rewrite files from scratch — make surgical changes.

## Your Workflow
1. Read the test files in tests/ to understand expected behavior (both old and new)
2. Read the existing implementation code to understand current structure
3. Read openspec/design.md for the change design
4. Make targeted modifications to existing files to satisfy new tests
5. Create new files ONLY when the design explicitly requires new modules
6. Run `pytest tests/ -v` after each significant change
7. Fix failures iteratively until ALL tests pass (both existing and new)

## Rules
- Do NOT modify any files in the tests/ directory
- Do NOT change existing function/class signatures unless the design explicitly requires it
- NEVER rewrite a file from scratch — make targeted edits
- Preserve existing code style, naming conventions, and patterns
- Run `pytest tests/ -v` to check progress — ALL tests must pass (regression + new)
- If you believe a TEST has a bug, explain in your finish message what is wrong.

When done, call finish with a summary of what was modified and test results.
"""

BLACKBOX_TEST_AGENT_MODIFY_PROMPT = """You are a black-box test engineer. Write executable pytest tests for CHANGED and NEW spec.md scenarios only.

## Context
This is a MODIFY operation. Existing blackbox tests cover unchanged scenarios.
Only write tests for new or changed scenarios.

## Your Workflow
1. Read openspec/spec.md to find NEW and CHANGED Given/When/Then scenarios
2. Read existing tests/test_blackbox_auto.py (if it exists) to see what's already covered
3. Read the implementation code to understand how to import and call the public API
4. Add new test functions to tests/test_blackbox_auto.py for changed/new scenarios
5. Tests must be REAL executable tests with actual assertions

## Rules
- ONLY create/edit files under the tests/ directory
- PRESERVE existing blackbox tests — only ADD new ones
- Test from the USER's perspective — treat the code as a black box
- Each test must have real assertions that verify the Then condition
- Do NOT use pytest.skip()
- If you believe IMPLEMENTATION CODE has a bug, explain in your finish message.

When done, call finish with a summary of tests created.
"""

TDD_CODE_AGENT_PROMPT = """You are an expert developer. Implement code to make all tests pass.

## Your Workflow
1. Read the test files in tests/ to understand expected behavior
2. Read the stub files to understand the required signatures
3. Read openspec/design.md for architecture context
4. Implement the code — fill in the stubs to make ALL tests pass
5. Run `pytest tests/ -v` after each significant change
6. Fix failures iteratively until all tests pass

## Rules
- Do NOT modify any files in the tests/ directory
- Do NOT change function/class signatures — they are contracts
- Run `pytest tests/ -v` to check progress
- Keep implementations clean and follow the design document
- Handle edge cases as specified in the tests
- If you believe a TEST has a bug (wrong data, contradictory logic), do NOT try to edit it.
  Instead, explain in your finish message what is wrong with the test and why.
  The system will route your request to the test agent for fixing.

When done, call finish with a summary of what was implemented and test results.
"""

BLACKBOX_TEST_AGENT_PROMPT = """You are a black-box test engineer. Write executable pytest tests based ONLY on specification scenarios.

## Your Workflow
1. Read openspec/spec.md to understand the Given/When/Then scenarios
2. Read the implementation code to understand how to import and call the public API
3. Write a single test file: tests/test_blackbox_auto.py
4. Each scenario becomes one test function: test_tc_001, test_tc_002, etc.
5. Tests must be REAL executable tests with actual assertions — NOT stubs or skips

## Rules
- ONLY create/edit files under the tests/ directory
- Test from the USER's perspective — treat the code as a black box
- Import the actual modules and call the real API
- Each test must have real assertions that verify the Then condition
- Do NOT use pytest.skip() — every test must run and assert something
- If you believe IMPLEMENTATION CODE has a bug, do NOT try to edit it.
  Instead, explain in your finish message what is wrong and why.
  The system will route your request to the coding agent for fixing.

When done, call finish with a summary of tests created.
"""

TEST_AGENT_VERIFY_PROMPT = """You are a test verification agent. Run tests and analyze results.

## Your Workflow
1. Run `pytest tests/ -v --tb=long` to execute all tests
2. Read implementation code to verify it matches the design
3. Analyze the results carefully
4. Report: which tests pass, which fail, and why

## Rules
- You CANNOT modify any files — you are read-only
- Focus on accurate reporting of test results
- Include the full pytest output in your report
- If tests fail, provide a clear failure analysis for the developer

When done, call finish with the complete test results and analysis.
"""

TDD_CODE_AGENT_WITH_DEBUG_PROMPT = """You are an expert developer. Implement code to make all tests pass.

You have received a Debug Report with hypotheses about the bug. Follow these hypotheses.

## Your Workflow
1. Read the Debug Report carefully — it contains fault localization and hypotheses
2. Focus on CONFIRMED hypotheses first, then SUSPICIOUS ones
3. Implement fixes based on the hypotheses
4. Run `pytest tests/ -v` after each fix
5. If your fix doesn't work and you believe a hypothesis is WRONG:
   Output in your finish message: [CHALLENGE:hyp_XXX] reason: <why the hypothesis is wrong>
   [EVIDENCE] <code or output that proves the hypothesis wrong>

## Rules
- Do NOT modify any files in the tests/ directory
- Do NOT change function/class signatures
- Address the hypotheses — don't ignore them
- If you challenge a hypothesis, provide CLEAR evidence
- If you believe a TEST has a bug (wrong data, contradictory logic), do NOT try to edit it.
  Instead, explain in your finish message what is wrong with the test and why.
  The system will route your request to the test agent for fixing.

When done, call finish with a summary and test results.
"""

DEBUG_AGENT_PROMPT = """You are a debug analyst. Analyze test failures using scientific debugging.

## Your Tools
- `fault_localize`: Run SBFL fault localization to rank suspicious code lines
- `probe_tool`: Insert diagnostic probes into source code
  - insert_trace: non-interrupting log (program runs to completion)
  - insert_halt: interrupting breakpoint (program exits at that point with code 99)
  - remove_all: restore all files to original state
  - collect: parse probe output from test run
- `hypothesis_tool`: Manage debug hypotheses
  - create: propose a new hypothesis
  - update: change status (confirmed/excluded/suspicious)
  - add_evidence: attach probe evidence to a hypothesis
- `terminal`: Run tests to collect probe output
- `FileReadTool`: Read source code

## Your Workflow
1. Run `fault_localize` with command="localize" to get suspicious line ranking
2. Read the suspicious code and the failing test output
3. Create 1-3 hypotheses about the bug cause using `hypothesis_tool`
4. For each hypothesis:
   a. Insert trace probes at suspicious locations using `probe_tool`
   b. Run the failing test: `pytest <test_file>::<test_name> -v -s 2>&1`
   c. Collect probe output using `probe_tool` command="collect"
   d. Add evidence to the hypothesis using `hypothesis_tool`
   e. Update hypothesis status based on evidence
   f. If needed, insert halt probes to narrow down further
5. After investigation, remove all probes: `probe_tool` command="remove_all"
6. Generate final report: `hypothesis_tool` command="report"

## Hypothesis Status Guide
- confirmed: Evidence clearly supports this is the bug cause
- excluded: Evidence proves this is NOT the cause
- suspicious: Evidence is inconclusive, needs more investigation

## Rules
- You CANNOT edit business code — only insert probes
- ALWAYS remove all probes before finishing
- Be systematic: one hypothesis at a time
- Provide clear reasoning for each status change

When done, call finish with your analysis summary.
"""

TEST_AGENT_FIX_PROMPT = """You are a test engineer. Fix test issues identified by debug analysis.

## Context
The debug analysis found that certain test failures are caused by issues in the
test code itself, not in the implementation. The coding agent attempted to fix
the tests but was blocked because it cannot edit files under tests/.

## Your Workflow
1. Read the Debug Report and boundary violation context carefully
2. Read the affected test files and the implementation code
3. Fix the test issues — preserve the TEST INTENT but fix the test DATA or LOGIC
4. Run `pytest tests/ -v` to verify your fixes don't break other tests
5. Do NOT modify any files outside the tests/ directory
6. Do NOT weaken tests — fix bugs in test data/logic, don't remove assertions

When done, call finish with a summary of what was fixed.
"""


# --- New flow prompts (v2 — Debug Form architecture) ---

TDD_CODE_AGENT_SMOKE_PROMPT = """You are an expert developer. Implement code based on design documents and stub signatures.

## Your Workflow
1. Read openspec/design.md for architecture and interface definitions
2. Read the stub files to understand required signatures
3. You may READ test files in tests/ to understand expected behavior — but you CANNOT edit or run them
4. Implement the code — fill in the stubs with real implementations
5. Your ONLY verification is a smoke test: import the module and print "ok"
6. Do NOT run pytest. Do NOT modify test files.

## Rules
- Do NOT modify any files in the tests/ directory
- Do NOT run `pytest` — you are not allowed to run tests
- Do NOT change function/class signatures — they are contracts
- Keep implementations clean and follow the design document
- Handle edge cases as you understand them from reading tests and design docs
- After implementing, run the smoke test command provided in the task message

When done, call finish with a summary of what was implemented.
"""

TDD_CODE_AGENT_SMOKE_MODIFY_PROMPT = """You are an expert developer. Modify existing code based on design changes.

## Context
This is a MODIFY operation on an existing codebase. Make targeted edits to existing files.

## Your Workflow
1. Read openspec/design.md for the change design
2. Read existing implementation code to understand current structure
3. You may READ test files in tests/ to understand expected behavior — but you CANNOT edit or run them
4. Make targeted modifications to existing files to satisfy the design changes
5. Create new files ONLY when the design explicitly requires new modules
6. Your ONLY verification is a smoke test: import the module and print "ok"
7. Do NOT run pytest. Do NOT modify test files.

## Rules
- Do NOT modify any files in the tests/ directory
- Do NOT run `pytest` — you are not allowed to run tests
- NEVER rewrite a file from scratch — make targeted edits
- Preserve existing code style, naming conventions, and patterns
- After implementing, run the smoke test command provided in the task message

When done, call finish with a summary of what was modified.
"""

TEST_AGENT_ANALYST_PROMPT = """You are a test failure analyst. Analyze test failures by filling structured Debug Forms.

## Context
The Coding Agent has written implementation code. All tests have been run automatically.
You are receiving the full test results. Your job is to analyze each failure and fill a Debug Form.

## Your Workflow
1. Read the test results carefully — understand which tests failed and why
2. Read the implementation code to understand what it actually does
3. Read the test source code to understand what each failing test expects
4. For each failing test, use `debug_form_tool` command="fill" to create a Debug Form:
   - test_id: the full test identifier
   - assertion_value: what was expected vs what was actual (e.g. "expected 3, got 3.0")
   - assertion_meaning: what this assertion is semantically checking
   - actual_situation: what the code actually does based on your reading
   - guessed_cause: (REQUIRED) your hypothesis about WHY it fails
   - surface_clues: observable symptoms
   - log_clues: evidence from test output
5. If many tests fail with the SAME root cause, use command="batch_fill":
   - batch_pattern: a regex matching the failing test names
   - batch_test_ids: comma-separated list of all affected test IDs
   - Fill the form once — it applies to all matched tests
6. If you believe YOUR OWN TEST is wrong (contradictory logic, impossible assertion),
   use command="flag_test_bug" to mark it
7. When all forms are filled, use command="submit" to finalize

## Rules
- Do NOT modify any files — you are read-only
- The `guessed_cause` field is REQUIRED for every form — you MUST hypothesize
- Use batch_fill when 3+ tests fail for the same reason — don't fill 50 individual forms
- Be honest: if a test looks wrong, flag it as a test bug
- Read BOTH the test code AND the implementation before guessing

When done, call finish with a summary of your analysis.
"""

TDD_CODE_AGENT_DEBUG_FIX_PROMPT = """You are an expert developer. Fix code based on structured Debug Forms from the test analyst.

## Context
You received Debug Forms analyzing test failures. Each form contains:
- What the test expected vs what happened
- A hypothesis about why it fails
- Evidence and clues

## Your Workflow
1. Read the Debug Forms carefully
2. For each form, judge if the hypothesis is reasonable:
   a. If reasonable: modify your code to fix the issue, then run the specific failing test to verify
   b. If the test itself is flagged as a potential bug: note it but still try to fix code first
3. Run failing tests one by one or in small groups to verify fixes
4. If you fix code and tests pass — great, continue to next form
5. If ALL hypotheses are wrong and you cannot make progress:
   Output in your finish message: [REJECT] reason: <why all hypotheses are wrong>
   [COUNTER_EVIDENCE] <code analysis or test output proving hypotheses wrong>

## Rules
- Do NOT modify any files in the tests/ directory
- Do NOT change function/class signatures unless the design explicitly requires it
- Address the Debug Forms systematically — don't ignore them
- If you reject, provide CLEAR counter-evidence
- Run `pytest <specific_test> -v` to verify each fix

When done, call finish with a summary of fixes applied and test results.
"""

ANTICHEAT_BLACKBOX_PROMPT = """You are an anti-cheat test engineer. Generate variant tests to prevent hardcoded solutions.

## Context
Certain tests just flipped from FAILING to PASSING after code changes.
This could mean the fix is legitimate, or the code might be "cheating" (e.g., hardcoding return values).
Your job: write additional blackbox tests covering the SAME behavior with DIFFERENT inputs.

## Your Workflow
1. Read the list of flipped tests and their source code
2. For each flipped test, understand what behavior it tests
3. Write a variant test that tests the SAME API/function but with DIFFERENT input values
4. The variant should verify the same contract but catch hardcoded solutions
5. Write all variants to the specified output file
6. Run the variant tests to verify they pass

## Rules
- ONLY create/edit files under the tests/ directory
- Each variant must use DIFFERENT inputs from the original test
- Variants must have real assertions — no pytest.skip()
- If a variant fails, that's useful information — it means the fix may be a hack

When done, call finish with a summary of variant tests created.
"""


# =============================================================================
# Agent creation
# =============================================================================

def create_test_agent_write(llm: LLM, mode: str = "create") -> Agent:
    """Create Test Agent in write mode — can only write under tests/."""
    prompt = TEST_AGENT_WRITE_MODIFY_PROMPT if mode == "modify" else TEST_AGENT_WRITE_PROMPT
    return Agent(
        llm=llm,
        tools=[
            {"name": "test_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": prompt},
    )


def create_code_agent(llm: LLM, mode: str = "create") -> Agent:
    """Create Code Agent — can write everywhere except tests/."""
    prompt = TDD_CODE_AGENT_MODIFY_PROMPT if mode == "modify" else TDD_CODE_AGENT_PROMPT
    return Agent(
        llm=llm,
        tools=[
            {"name": "code_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": prompt},
    )


def create_blackbox_test_agent(llm: LLM, mode: str = "create") -> Agent:
    """Create agent to write real blackbox tests from spec.md scenarios."""
    prompt = BLACKBOX_TEST_AGENT_MODIFY_PROMPT if mode == "modify" else BLACKBOX_TEST_AGENT_PROMPT
    return Agent(
        llm=llm,
        tools=[
            {"name": "test_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": prompt},
    )


def create_test_agent_fix(llm: LLM) -> Agent:
    """Create Test Agent in fix mode — can edit tests/ to fix test bugs."""
    return Agent(
        llm=llm,
        tools=[
            {"name": "test_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": TEST_AGENT_FIX_PROMPT},
    )


def create_test_agent_verify(llm: LLM) -> Agent:
    """Create Test Agent in verify mode — read + run only, no file writes."""
    return Agent(
        llm=llm,
        tools=[
            {"name": FileReadTool.name},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": TEST_AGENT_VERIFY_PROMPT},
    )


def create_debug_agent(llm: LLM) -> Agent:
    """Create Debug Agent — probes + fault localization + hypotheses, read-only for code."""
    return Agent(
        llm=llm,
        tools=[
            {"name": "probe_tool"},
            {"name": "fault_localize"},
            {"name": "hypothesis_tool"},
            {"name": FileReadTool.name},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": DEBUG_AGENT_PROMPT},
    )


def create_code_agent_with_debug(llm: LLM) -> Agent:
    """Create Code Agent that receives debug hypotheses."""
    return Agent(
        llm=llm,
        tools=[
            {"name": "code_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": TDD_CODE_AGENT_WITH_DEBUG_PROMPT},
    )


# --- New flow agent factories (v2 — Debug Form architecture) ---

def create_code_agent_smoke(llm: LLM, mode: str = "create") -> Agent:
    """Create Code Agent in smoke-test-only mode — can write code, cannot run pytest."""
    prompt = TDD_CODE_AGENT_SMOKE_MODIFY_PROMPT if mode == "modify" else TDD_CODE_AGENT_SMOKE_PROMPT
    return Agent(
        llm=llm,
        tools=[
            {"name": "code_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": prompt},
    )


def create_test_agent_analyst(llm: LLM) -> Agent:
    """Create Test Agent in analyst mode — fills Debug Forms, read-only."""
    return Agent(
        llm=llm,
        tools=[
            {"name": "debug_form_tool"},
            {"name": FileReadTool.name},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": TEST_AGENT_ANALYST_PROMPT},
    )


def create_code_agent_debug_fix(llm: LLM) -> Agent:
    """Create Code Agent that receives Debug Forms and can run failing tests."""
    return Agent(
        llm=llm,
        tools=[
            {"name": "code_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": TDD_CODE_AGENT_DEBUG_FIX_PROMPT},
    )


def create_anticheat_agent(llm: LLM) -> Agent:
    """Create agent to write anti-cheat variant tests for flipped tests."""
    return Agent(
        llm=llm,
        tools=[
            {"name": "test_file_editor"},
            {"name": "terminal"},
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={"custom_prompt": ANTICHEAT_BLACKBOX_PROMPT},
    )


# =============================================================================
# Pytest output parser
# =============================================================================

def parse_pytest_output(output: str) -> TestRunResult:
    """Parse pytest output to extract pass/fail counts."""
    # Match summary line like "5 passed, 2 failed, 1 error"
    passed = 0
    failed = 0
    errors = 0

    # Try the summary line pattern
    summary_match = re.search(
        r"(\d+)\s+passed", output
    )
    if summary_match:
        passed = int(summary_match.group(1))

    fail_match = re.search(r"(\d+)\s+failed", output)
    if fail_match:
        failed = int(fail_match.group(1))

    error_match = re.search(r"(\d+)\s+error", output)
    if error_match:
        errors = int(error_match.group(1))

    total = passed + failed + errors
    all_passed = total > 0 and failed == 0 and errors == 0

    return TestRunResult(
        all_passed=all_passed,
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
        output=output,
    )


def _parse_per_test_results(output: str) -> list[PerTestResult]:
    """Parse pytest -v output into per-test results.

    Matches lines like:
      tests/test_calc.py::test_add PASSED
      tests/test_calc.py::TestClass::test_method FAILED
    Also extracts failure messages from FAILURES section.
    """
    results: list[PerTestResult] = []
    seen: set[str] = set()

    # Parse per-test status lines
    for line in output.split("\n"):
        line = line.strip()
        # Match: path::test_name or path::Class::test_name STATUS
        m = re.match(r"([\w/\\._-]+::[\w_]+(?:::[\w_]+)?)\s+(PASSED|FAILED|ERROR|SKIPPED)", line)
        if m:
            test_id = m.group(1)
            status = m.group(2).lower()
            if test_id not in seen:
                seen.add(test_id)
                results.append(PerTestResult(test_id=test_id, status=status))

    # Extract failure messages from FAILURES section
    failure_blocks: dict[str, str] = {}
    in_failures = False
    current_test = ""
    current_lines: list[str] = []
    for line in output.split("\n"):
        if line.strip().startswith("= FAILURES =") or line.strip().startswith("=== FAILURES ==="):
            in_failures = True
            continue
        if in_failures and line.strip().startswith("= short test summary") or line.strip().startswith("==="):
            if current_test and current_lines:
                failure_blocks[current_test] = "\n".join(current_lines)
            break
        if in_failures:
            # Match: ___ test_name ___
            fm = re.match(r"_{3,}\s*([\w/\\._:-]+)\s*_{3,}", line)
            if fm:
                if current_test and current_lines:
                    failure_blocks[current_test] = "\n".join(current_lines)
                current_test = fm.group(1).strip()
                current_lines = []
            elif current_test:
                current_lines.append(line)

    # Attach failure messages to results
    for r in results:
        if r.status in ("failed", "error"):
            # Try exact match, then partial match on test name
            test_name = r.test_id.split("::")[-1] if "::" in r.test_id else r.test_id
            msg = failure_blocks.get(r.test_id, "") or failure_blocks.get(test_name, "")
            if msg:
                r.failure_message = msg[:2000]

    return results


def run_tests_automated(
    workspace: Path,
    test_dirs: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
    timeout: int = 300,
) -> TestRunResult:
    """Run all tests automatically via subprocess — no agent involved.

    This is Phase 4: pure automation, no LLM.
    """
    if test_dirs is None:
        test_dirs = ["tests/"]

    cmd = ["python3", "-m", "pytest"] + test_dirs + ["-v", "--tb=long"]
    for pat in (ignore_patterns or []):
        cmd.extend(["--ignore", pat])

    try:
        result = subprocess.run(
            cmd, cwd=workspace, capture_output=True, text=True, timeout=timeout,
        )
        combined = result.stdout + "\n" + result.stderr
    except subprocess.TimeoutExpired:
        combined = f"pytest timed out after {timeout}s"
    except Exception as e:
        combined = f"pytest execution error: {e}"

    parsed = parse_pytest_output(combined)
    parsed.per_test = _parse_per_test_results(combined)
    return parsed


def _extract_test_output_from_conversation(conversation: Conversation) -> str:
    """Extract pytest output from conversation events.

    Walks the event log looking for:
    1. Terminal ObservationEvents containing pytest output
    2. The FinishAction message as fallback
    """
    from openhands.sdk.event import ActionEvent, ObservationEvent
    from openhands.sdk.tool.builtins.finish import FinishAction

    terminal_outputs: list[str] = []
    finish_message = ""

    try:
        for event in conversation.state.events:
            # Terminal observation — contains command output
            if isinstance(event, ObservationEvent) and event.tool_name == "terminal":
                for item in event.observation.content:
                    text = getattr(item, "text", "")
                    if text:
                        terminal_outputs.append(text)

            # Finish action — agent's final summary
            if isinstance(event, ActionEvent) and isinstance(event.action, FinishAction):
                finish_message = event.action.message or ""
    except Exception:
        pass

    # Prefer terminal output that contains pytest summary lines
    pytest_outputs = [
        t for t in terminal_outputs
        if "passed" in t or "failed" in t or "error" in t
    ]

    if pytest_outputs:
        return "\n".join(pytest_outputs)

    # Fallback: all terminal output
    if terminal_outputs:
        return "\n".join(terminal_outputs)

    # Last resort: the finish message may contain test results
    if finish_message:
        return finish_message

    return "No pytest output captured"


# =============================================================================
# Phase 5: Black-box test generation from spec.md
# =============================================================================

def generate_blackbox_tests(workspace: Path) -> Path | None:
    """Generate pytest black-box tests from spec.md Given/When/Then scenarios.

    Returns path to generated test file, or None if no spec.md found.
    """
    spec_path = workspace / "openspec" / "spec.md"
    if not spec_path.exists():
        return None

    spec_md = spec_path.read_text(encoding="utf-8")
    scenarios = _parse_spec_scenarios(spec_md)

    if not scenarios:
        return None

    lines = [
        '"""Auto-generated black-box tests from spec.md scenarios."""',
        "",
        "import pytest",
        "",
        "# NOTE: These tests are generated from Given/When/Then scenarios.",
        "# The test bodies are intentionally left as stubs — the Test Agent",
        "# or Code Agent should fill them in based on the scenario descriptions.",
        "",
    ]

    for scenario in scenarios:
        func_name = _scenario_to_func_name(scenario["id"])
        lines.append(f"def {func_name}():")
        lines.append(f'    """')
        lines.append(f'    {scenario["name"]}')
        lines.append(f'    Given: {scenario["given"]}')
        lines.append(f'    When: {scenario["when"]}')
        lines.append(f'    Then: {scenario["then"]}')
        lines.append(f'    """')
        lines.append(f'    # TODO: Implement based on scenario')
        lines.append(f'    pytest.skip("Black-box test not yet implemented")')
        lines.append("")

    test_dir = workspace / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / "test_blackbox_auto.py"
    test_file.write_text("\n".join(lines), encoding="utf-8")
    return test_file


def _parse_spec_scenarios(spec_md: str) -> list[dict[str, str]]:
    """Parse Given/When/Then scenarios from spec.md."""
    scenarios: list[dict[str, str]] = []
    lines = spec_md.split("\n")
    current: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        # Match "## TC-001: Name"
        if stripped.startswith("## "):
            if current:
                scenarios.append(current)
            header = stripped[3:].strip()
            parts = header.split(":", 1)
            current = {
                "id": parts[0].strip(),
                "name": parts[1].strip() if len(parts) > 1 else header,
                "given": "",
                "when": "",
                "then": "",
            }
        elif stripped.startswith("**Given:**"):
            if current:
                current["given"] = stripped.replace("**Given:**", "").strip()
        elif stripped.startswith("**When:**"):
            if current:
                current["when"] = stripped.replace("**When:**", "").strip()
        elif stripped.startswith("**Then:**"):
            if current:
                current["then"] = stripped.replace("**Then:**", "").strip()

    if current:
        scenarios.append(current)

    return scenarios


def _scenario_to_func_name(scenario_id: str) -> str:
    """Convert scenario ID to a valid pytest function name."""
    # "TC-001" -> "test_tc_001"
    name = scenario_id.lower().replace("-", "_").replace(" ", "_")
    if not name.startswith("test_"):
        name = f"test_{name}"
    return name


# =============================================================================
# Main orchestrator
# =============================================================================

def run_tdd_pipeline(
    workspace: str | Path,
    llm: LLM | None = None,
    language: str = "python",
    mode: str = "create",
    project_id: str | None = None,
    change_request: str | None = None,
    log_dir: Path | None = None,
) -> TDDResult:
    """Run the TDD pipeline: signatures → tests → code → verify.

    Args:
        workspace: Directory containing openspec/ design documents
        llm: LLM instance (created from config if not provided)
        language: Target language
        mode: "create" for greenfield, "modify" for brownfield
        project_id: Project ID for loading architecture (modify mode)
        change_request: Description of changes (modify mode)
        log_dir: If set, save agent conversation logs to this directory

    Returns:
        TDDResult with full pipeline results
    """
    from toyshop import create_toyshop_llm

    if llm is None:
        llm = create_toyshop_llm()

    workspace = Path(workspace)

    # ── Phase 1: Signature Extraction ──
    print("[TDD] Phase 1: Signature Extraction")
    manifest = extract_signatures(workspace, mode=mode)
    print(f"  Stubs: {manifest.stub_files}")
    print(f"  Interfaces: {len(manifest.interfaces)}")
    if mode == "modify":
        print(f"  Mode: modify (preserving existing code)")

    if not manifest.interfaces:
        return TDDResult(
            success=False,
            stub_files=manifest.stub_files,
            summary="No interfaces found in design.md — cannot generate stubs",
        )

    # ── Phase 2: Test Generation ──
    print("[TDD] Phase 2: Test Generation (Test Agent — write mode)")
    test_agent = create_test_agent_write(llm, mode=mode)
    test_conv = Conversation(agent=test_agent, workspace=str(workspace))

    stub_list = "\n".join(f"  - {f}" for f in manifest.stub_files)
    modify_hint = ""
    if mode == "modify":
        modify_hint = (
            "\nThis is a MODIFY operation. Existing tests cover unchanged interfaces.\n"
            "Only write tests for NEW and CHANGED interfaces as described in the design docs.\n"
            "Preserve all existing test files.\n"
        )
    test_conv.send_message(
        f"Write comprehensive pytest tests for this project.\n\n"
        f"Design documents are in openspec/ directory.\n"
        f"Stub files with signatures:\n{stub_list}\n"
        f"{modify_hint}\n"
        f"Create test files in the tests/ directory."
    )
    test_conv.run()
    if log_dir:
        _save_agent_log(test_conv, log_dir, "phase2_test")

    # Check if test agent tried to edit business code
    test_violations = _detect_boundary_violations(test_conv, "test")
    if test_violations:
        code_paths = [v.target_path for v in test_violations]
        print(f"  [BOUNDARY] Test agent attempted to edit code file(s): {code_paths}")

    # Collect test files
    test_dir = workspace / "tests"
    test_files = sorted(
        str(f.relative_to(workspace))
        for f in test_dir.rglob("test_*.py")
        if f.name != "test_blackbox_auto.py"
    )
    print(f"  Test files created: {test_files}")

    if not test_files:
        return TDDResult(
            success=False,
            stub_files=manifest.stub_files,
            summary="Test Agent produced no test files",
        )

    # ── Phase 5 (early): Check if spec.md has scenarios for black-box ──
    spec_path = workspace / "openspec" / "spec.md"
    has_spec_scenarios = spec_path.exists() and _parse_spec_scenarios(
        spec_path.read_text(encoding="utf-8")
    )
    if has_spec_scenarios:
        print("[TDD] spec.md has scenarios — black-box tests will be generated after white-box passes")
    else:
        print("[TDD] No spec.md scenarios found, skipping black-box tests")

    # ── Retry loop: Phase 3 → Phase 4 → Phase 4.5/4.6/4.7 → Phase 5 ──
    # v2 Debug Form architecture
    whitebox_passed = False
    blackbox_passed = False
    whitebox_output = ""
    blackbox_output = ""
    all_debug_reports: list[DebugReport] = []
    all_legacy_issues: list[LegacyIssue] = []
    all_attempts: list[str] = []
    all_debug_form_sets: list[DebugFormSet] = []
    bb_test_file: Path | None = None

    # Initialize rollback manager
    rollback = RollbackManager(workspace)
    rollback.checkpoint("pipeline_start")

    # ── Phase 3: Code Implementation (smoke test only) ──
    print("[TDD] Phase 3: Code Implementation (smoke test only)")
    pre_code_checkpoint = rollback.checkpoint("phase3_start")

    # Build smoke test command from stub modules
    stub_modules = []
    for sf in manifest.stub_files:
        p = Path(sf)
        # Convert path like "kvstore/stubs.py" to "kvstore.stubs"
        parts = list(p.parts)
        if parts[-1].endswith(".py"):
            parts[-1] = parts[-1][:-3]
        stub_modules.append(".".join(parts))
    smoke_imports = "; ".join(f"import {m}" for m in stub_modules) if stub_modules else "print('no stubs')"
    smoke_cmd = f"python3 -c '{smoke_imports}; print(\"smoke ok\")'"

    code_agent = create_code_agent_smoke(llm, mode=mode)
    code_conv = Conversation(agent=code_agent, workspace=str(workspace))
    modify_hint = ""
    if mode == "modify":
        modify_hint = (
            "\nThis is a MODIFY operation. Make targeted edits to existing files.\n"
            "Do NOT rewrite files from scratch.\n"
        )
    code_conv.send_message(
        f"Implement the code to satisfy the design.\n\n"
        f"Design documents: openspec/\n"
        f"Stub files: {stub_list}\n"
        f"Test files (read-only, for reference): {', '.join(test_files)}\n"
        f"{modify_hint}\n"
        f"After implementing, run this smoke test:\n  {smoke_cmd}\n"
        f"Do NOT run pytest."
    )
    code_conv.run()
    if log_dir:
        _save_agent_log(code_conv, log_dir, "phase3_code_smoke")

    finish_msg = _extract_finish_message(code_conv)
    all_attempts.append(f"Phase 3 smoke: {finish_msg[:200]}")
    rollback.checkpoint("phase3_end")

    # ── Phase 4: Auto Test Run (no agent) ──
    print("[TDD] Phase 4: Auto Test Run (all whitebox + blackbox)")
    ignore_pats = [
        "tests/test_blackbox_auto.py",
        "tests/test_whitebox_from_bb.py",
        "tests/test_blackbox_variants.py",
    ]
    # Also ignore any anticheat files from previous runs
    for ac in workspace.glob("tests/test_anticheat_*.py"):
        ignore_pats.append(str(ac.relative_to(workspace)))

    wb_results = run_tests_automated(workspace, ["tests/"], ignore_pats)
    whitebox_output = wb_results.output
    print(f"  White-box: {wb_results.passed} passed, {wb_results.failed} failed, {wb_results.errors} errors")

    if wb_results.all_passed:
        whitebox_passed = True
    else:
        # ── Debug Form loop ──
        debug_retry = 0
        baseline_results = wb_results
        previous_rejection: Rejection | None = None

        while debug_retry < MAX_DEBUG_FORM_RETRIES:
            # Phase 4.5: Test Agent fills Debug Forms
            form_set = _run_debug_form_analysis(
                workspace, llm, baseline_results, previous_rejection, log_dir,
                round_num=debug_retry + 1,
            )
            all_debug_form_sets.append(form_set)

            if not form_set.forms:
                print("  No forms filled — cannot proceed with debug")
                break

            # Phase 4.6: Code Agent fixes with forms (with rejection loop)
            rejection_count = 0
            made_changes = False
            while rejection_count < MAX_REJECTION_RETRIES:
                pre_fix_checkpoint = rollback.checkpoint(f"phase4_6_round{debug_retry + 1}")
                made_changes, rejection = _run_debug_fix(
                    workspace, llm, form_set, baseline_results, mode, log_dir,
                    round_num=debug_retry + 1,
                )
                if rejection:
                    rejection_count += 1
                    previous_rejection = rejection
                    if rejection_count < MAX_REJECTION_RETRIES:
                        print(f"  Rejection {rejection_count}/{MAX_REJECTION_RETRIES} — re-analyzing")
                        rollback.rollback_to(pre_fix_checkpoint)
                        # Re-run Phase 4.5 with rejection context
                        form_set = _run_debug_form_analysis(
                            workspace, llm, baseline_results, previous_rejection, log_dir,
                            round_num=debug_retry + 1,
                        )
                        all_debug_form_sets.append(form_set)
                    else:
                        print(f"  Rejection retries exhausted ({MAX_REJECTION_RETRIES})")
                else:
                    previous_rejection = None
                    break

            # Phase 4.7: Anti-cheat for flipped tests
            after_results = run_tests_automated(workspace, ["tests/"], ignore_pats)
            anticheat_files = _run_anticheat(
                workspace, llm, baseline_results, after_results,
                round_num=debug_retry + 1, log_dir=log_dir,
            )

            # Re-run all tests (including anticheat)
            all_ignore = [p for p in ignore_pats if "anticheat" not in p]
            final_results = run_tests_automated(workspace, ["tests/"], all_ignore)
            whitebox_output = final_results.output
            print(f"  After round {debug_retry + 1}: {final_results.passed} passed, {final_results.failed} failed")

            if final_results.all_passed:
                whitebox_passed = True
                break

            # Prepare for next round
            baseline_results = final_results
            debug_retry += 1
            all_attempts.append(f"Debug round {debug_retry}: {final_results.failed} still failing")

        if not whitebox_passed:
            print(f"  Debug form retries exhausted ({MAX_DEBUG_FORM_RETRIES})")
            _mark_legacy_issues(
                whitebox_output, all_debug_reports, all_attempts, all_legacy_issues
            )

    # ── Phase 5: Black-box Tests ──
    if whitebox_passed and has_spec_scenarios:
        print("[TDD] Phase 5a: Black-box Test Generation (agent writes from spec.md)")
        bb_write_agent = create_blackbox_test_agent(llm, mode=mode)
        bb_write_conv = Conversation(agent=bb_write_agent, workspace=str(workspace))
        modify_hint = ""
        if mode == "modify":
            modify_hint = (
                "\nThis is a MODIFY operation. Only write tests for NEW and CHANGED scenarios.\n"
                "Preserve existing blackbox tests.\n"
            )
        bb_write_conv.send_message(
            "Write executable black-box tests from the spec.md scenarios.\n\n"
            "Read openspec/spec.md for the Given/When/Then scenarios.\n"
            "Read the implementation code to understand how to import modules.\n\n"
            "Create tests/test_blackbox_auto.py with one test per scenario.\n"
            "Each test must have REAL assertions — no pytest.skip().\n"
            f"{modify_hint}"
            "Run `pytest tests/test_blackbox_auto.py -v` to verify they pass."
        )
        bb_write_conv.run()
        if log_dir:
            _save_agent_log(bb_write_conv, log_dir, "phase5a_bb_write")

        bb_violations = _detect_boundary_violations(bb_write_conv, "test")
        if bb_violations:
            code_paths = [v.target_path for v in bb_violations]
            print(f"  [BOUNDARY] Blackbox test agent attempted to edit code file(s): {code_paths}")

        bb_test_file = workspace / "tests" / "test_blackbox_auto.py"

        if bb_test_file.exists():
            print("[TDD] Phase 5b: Black-box Verification (auto)")
            bb_results = run_tests_automated(
                workspace, ["tests/test_blackbox_auto.py"],
            )
            blackbox_output = bb_results.output
            print(f"  Black-box: {bb_results.passed} passed, {bb_results.failed} failed, {bb_results.errors} errors")

            if bb_results.all_passed:
                blackbox_passed = True
            elif bb_results.failed > 0:
                # Expose failing BB tests as WB + generate variants
                failing_bb_tests = _extract_failing_test_names(blackbox_output)
                if failing_bb_tests:
                    print(f"  Exposing {len(failing_bb_tests)} BB tests as WB + generating variants")
                    wb_from_bb = workspace / "tests" / "test_whitebox_from_bb.py"
                    expose_as_whitebox(failing_bb_tests, bb_test_file, wb_from_bb)

                    scenarios = _parse_spec_scenarios(
                        spec_path.read_text(encoding="utf-8")
                    )
                    variant_file = workspace / "tests" / "test_blackbox_variants.py"
                    generate_variant_tests_for_failures(
                        failing_bb_tests, bb_test_file, scenarios, llm, variant_file
                    )
                _mark_legacy_issues(
                    blackbox_output, all_debug_reports, all_attempts, all_legacy_issues
                )
        else:
            blackbox_passed = True  # no BB test file = skip
    elif whitebox_passed:
        blackbox_passed = True  # no spec scenarios = skip

    # ── Collect results ──
    files_created = []
    for f in workspace.rglob("*"):
        if f.is_file():
            rel = f.relative_to(workspace)
            if not str(rel).startswith((".toyshop", "openspec", "__pycache__", ".git", ".coverage", ".tdd_debug")):
                files_created.append(str(rel))

    all_test_files = sorted(
        str(f.relative_to(workspace)) for f in test_dir.rglob("test_*.py")
    )

    success = whitebox_passed and blackbox_passed
    legacy_count = len(all_legacy_issues)
    debug_form_count = sum(len(fs.forms) for fs in all_debug_form_sets)
    summary_parts = [
        f"TDD pipeline {'PASSED' if success else 'FAILED'}",
        f"White-box: {'PASSED' if whitebox_passed else 'FAILED'}",
        f"Black-box: {'PASSED' if blackbox_passed else 'SKIPPED' if not has_spec_scenarios else 'FAILED'}",
        f"Debug forms: {debug_form_count}",
    ]
    if legacy_count:
        summary_parts.append(f"Legacy issues: {legacy_count}")

    return TDDResult(
        success=success,
        files_created=files_created,
        test_files=all_test_files,
        stub_files=manifest.stub_files,
        whitebox_passed=whitebox_passed,
        blackbox_passed=blackbox_passed,
        whitebox_output=whitebox_output,
        blackbox_output=blackbox_output,
        summary=" | ".join(summary_parts),
        retry_count=len(all_debug_form_sets),
        legacy_issues=all_legacy_issues,
        debug_reports=all_debug_reports,
    )


# =============================================================================
# Debug analysis helper (Phase 4.5)
# =============================================================================

def _run_debug_analysis(
    workspace: Path,
    llm: LLM,
    test_output: str,
    challenge: CodingChallenge | None,
    all_debug_reports: list[DebugReport],
) -> DebugReport:
    """Run Phase 4.5: Debug Agent analyzes failures with probes and hypotheses."""
    print("[TDD] Phase 4.5: Debug Analysis")

    # Reset probe and hypothesis state for this session
    instrumentor = get_instrumentor(workspace)
    instrumentor.remove_all_probes()
    reset_probe_counter()
    hyp_manager = get_hypothesis_manager(workspace)
    hyp_manager.reset()

    rollback = RollbackManager(workspace)
    rollback.checkpoint("debug_start")

    # Build debug prompt
    debug_prompt = (
        f"Analyze these test failures and identify the bug.\n\n"
        f"## Test Output\n```\n{test_output[:3000]}\n```\n\n"
    )
    if challenge:
        debug_prompt += (
            f"## IMPORTANT: Previous hypothesis was challenged\n"
            f"Hypothesis {challenge.hypothesis_id} was challenged by the developer.\n"
            f"Reason: {challenge.challenge_reason}\n"
            f"Evidence: {challenge.evidence}\n\n"
            f"You must investigate from a DIFFERENT angle. "
            f"Do NOT repeat the same hypothesis.\n\n"
        )

    debug_prompt += (
        "Use fault_localize, probe_tool, and hypothesis_tool to investigate.\n"
        "Remember to remove all probes before finishing."
    )

    debug_agent = create_debug_agent(llm)
    debug_conv = Conversation(agent=debug_agent, workspace=str(workspace))
    debug_conv.send_message(debug_prompt)
    debug_conv.run()

    # Ensure probes are cleaned up
    cleaned = instrumentor.remove_all_probes()
    if cleaned > 0:
        print(f"  Cleaned up {cleaned} probe-modified files")

    rollback.checkpoint("debug_end")

    # Build DebugReport from hypothesis manager state
    active, excluded = hyp_manager.get_report()
    finish_msg = _extract_finish_message(debug_conv)

    report = DebugReport(
        failing_tests=_extract_failing_test_names(test_output),
        test_output=test_output[:2000],
        hypotheses=active,
        excluded_hypotheses=excluded,
        recommended_fix=finish_msg[:1000] if finish_msg else "",
    )

    # Add fault localization data if available
    try:
        localizer = FaultLocalizer(workspace)
        suspicious = localizer.localize(top_n=10)
        report.fault_localization = [
            {"file": s.file, "line": s.line, "score": s.score}
            for s in suspicious
        ]
    except Exception:
        pass

    all_debug_reports.append(report)
    print(f"  Hypotheses: {len(active)} active, {len(excluded)} excluded")
    return report


# =============================================================================
# v2 Debug Form helpers (Phase 4.5 / 4.6 / 4.7)
# =============================================================================

def _run_debug_form_analysis(
    workspace: Path,
    llm: LLM,
    test_results: TestRunResult,
    previous_rejection: Rejection | None,
    log_dir: Path | None,
    round_num: int = 1,
) -> DebugFormSet:
    """Phase 4.5: Test Agent fills Debug Forms for each failing test."""
    print(f"[TDD] Phase 4.5: Debug Form Analysis (round {round_num})")

    # Reset form executor for this round
    reset_debug_form_executor(workspace)

    # Build failure summary for the analyst
    failing = [r for r in test_results.per_test if r.status in ("failed", "error")]
    failure_text = f"## Test Results: {test_results.passed} passed, {test_results.failed} failed, {test_results.errors} errors\n\n"
    failure_text += "### Failing Tests\n"
    for r in failing:
        failure_text += f"\n**{r.test_id}** [{r.status}]\n"
        if r.failure_message:
            failure_text += f"```\n{r.failure_message[:1000]}\n```\n"

    # Add rejection context if this is a re-analysis
    rejection_context = ""
    if previous_rejection:
        rejection_context = (
            f"\n## REJECTION from Coding Agent\n"
            f"Your previous hypotheses were rejected.\n"
            f"Counter-evidence: {previous_rejection.counter_evidence}\n"
            f"Code analysis: {previous_rejection.code_analysis}\n\n"
            f"Re-analyze from a DIFFERENT angle. Do NOT repeat the same guesses.\n"
        )

    analyst_agent = create_test_agent_analyst(llm)
    analyst_conv = Conversation(agent=analyst_agent, workspace=str(workspace))
    analyst_conv.send_message(
        f"Analyze these test failures and fill Debug Forms.\n\n"
        f"{failure_text}\n"
        f"## Full Test Output\n```\n{test_results.output[:4000]}\n```\n"
        f"{rejection_context}\n"
        f"Read the implementation code and test source to understand each failure.\n"
        f"Use debug_form_tool to fill a form for each failing test (guessed_cause is REQUIRED).\n"
        f"Use batch_fill if many tests fail for the same root cause.\n"
        f"Call submit when done."
    )
    analyst_conv.run()
    if log_dir:
        _save_agent_log(analyst_conv, log_dir, f"phase4_5_analyst_round{round_num}")

    # Collect forms from executor
    executor = get_debug_form_executor(workspace)
    form_set = executor.form_set
    flagged = sum(1 for f in form_set.forms if f.flagged_as_test_bug)
    print(f"  Forms: {len(form_set.forms)} filled, {flagged} flagged as test bugs")
    return form_set


def _run_debug_fix(
    workspace: Path,
    llm: LLM,
    debug_forms: DebugFormSet,
    test_results: TestRunResult,
    mode: str,
    log_dir: Path | None,
    round_num: int = 1,
) -> tuple[bool, Rejection | None]:
    """Phase 4.6: Coding Agent receives Debug Forms, fixes code or rejects.

    Returns (made_changes: bool, rejection: Rejection | None).
    """
    print(f"[TDD] Phase 4.6: Debug Fix (round {round_num})")

    fix_agent = create_code_agent_debug_fix(llm)
    fix_conv = Conversation(agent=fix_agent, workspace=str(workspace))

    modify_hint = ""
    if mode == "modify":
        modify_hint = (
            "\nThis is a MODIFY operation. Make targeted edits to existing files.\n"
            "Do NOT rewrite files from scratch.\n"
        )

    fix_conv.send_message(
        f"Fix code based on these Debug Forms from the test analyst.\n\n"
        f"{debug_forms.to_prompt_text()}\n\n"
        f"## Test Summary\n"
        f"{test_results.passed} passed, {test_results.failed} failed, {test_results.errors} errors\n"
        f"{modify_hint}\n"
        f"Design documents: openspec/\n"
        f"Run failing tests individually with `pytest <test_id> -v` to verify fixes.\n"
        f"If ALL hypotheses are wrong, output [REJECT] reason: ... with [COUNTER_EVIDENCE] ..."
    )
    fix_conv.run()
    if log_dir:
        _save_agent_log(fix_conv, log_dir, f"phase4_6_fix_round{round_num}")

    # Check for rejection
    finish_msg = _extract_finish_message(fix_conv)
    rejection = parse_rejection_from_finish(finish_msg)
    if rejection:
        print(f"  [REJECT] {rejection.counter_evidence[:100]}")
        return False, rejection

    # Check for boundary violations (code agent tried to edit tests)
    violations = _detect_boundary_violations(fix_conv, "code")
    if violations:
        test_paths = [v.target_path for v in violations]
        print(f"  [BOUNDARY] Code agent attempted to edit test file(s): {test_paths}")

    print(f"  Fix applied (finish: {finish_msg[:100]}...)")
    return True, None


def _run_anticheat(
    workspace: Path,
    llm: LLM,
    before_results: TestRunResult,
    after_results: TestRunResult,
    round_num: int,
    log_dir: Path | None,
) -> list[str]:
    """Phase 4.7: Generate anti-cheat blackbox tests for flipped tests.

    Returns list of new test file paths.
    """
    flipped = _identify_flipped_tests(before_results, after_results)
    if not flipped:
        print("[TDD] Phase 4.7: No flipped tests — skipping anti-cheat")
        return []

    print(f"[TDD] Phase 4.7: Anti-cheat for {len(flipped)} flipped tests")

    # Build context about flipped tests
    flipped_info = "## Flipped Tests (were FAILING, now PASSING)\n\n"
    for test_id in flipped:
        flipped_info += f"- {test_id}\n"

    output_file = f"tests/test_anticheat_round{round_num}.py"

    anticheat_agent = create_anticheat_agent(llm)
    anticheat_conv = Conversation(agent=anticheat_agent, workspace=str(workspace))
    anticheat_conv.send_message(
        f"Generate anti-cheat variant tests for these flipped tests.\n\n"
        f"{flipped_info}\n"
        f"Read the source code of each flipped test to understand what it tests.\n"
        f"Write variant tests with DIFFERENT inputs to {output_file}.\n"
        f"Run `pytest {output_file} -v` to verify they pass."
    )
    anticheat_conv.run()
    if log_dir:
        _save_agent_log(anticheat_conv, log_dir, f"phase4_7_anticheat_round{round_num}")

    new_files = []
    ac_path = workspace / output_file
    if ac_path.exists():
        new_files.append(output_file)
        print(f"  Created {output_file}")
    else:
        print(f"  Warning: {output_file} not created")

    return new_files


def _identify_flipped_tests(
    before: TestRunResult, after: TestRunResult,
) -> list[str]:
    """Find tests that went from failing/error to passing."""
    before_failing = {
        r.test_id for r in before.per_test if r.status in ("failed", "error")
    }
    after_passing = {
        r.test_id for r in after.per_test if r.status == "passed"
    }
    return sorted(before_failing & after_passing)


def _extract_finish_message(conversation: Conversation) -> str:
    """Extract the finish message from a conversation."""
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.tool.builtins.finish import FinishAction

    try:
        for event in conversation.state.events:
            if isinstance(event, ActionEvent) and isinstance(event.action, FinishAction):
                return event.action.message or ""
    except Exception:
        pass
    return ""


def _save_agent_log(
    conversation: Conversation, log_dir: Path, phase_name: str,
) -> None:
    """Save conversation events as a readable log file."""
    from openhands.sdk.event import ActionEvent, ObservationEvent

    log_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    try:
        for event in conversation.state.events:
            if isinstance(event, ActionEvent):
                lines.append(f"[ACTION] {event.action.__class__.__name__}")
                if hasattr(event.action, "message") and event.action.message:
                    lines.append(f"  {event.action.message[:500]}")
            elif isinstance(event, ObservationEvent):
                text = ""
                if hasattr(event, "observation") and hasattr(event.observation, "content"):
                    for item in event.observation.content:
                        if hasattr(item, "text"):
                            text += item.text
                if text:
                    lines.append(f"[OBS] {text[:1000]}")
    except Exception:
        lines.append("[ERROR] Failed to extract some events")

    (log_dir / f"{phase_name}.log").write_text("\n".join(lines), encoding="utf-8")


def _extract_failing_test_names(test_output: str) -> list[str]:
    """Extract failing test names from pytest output."""
    failing = []
    for line in test_output.split("\n"):
        if "FAILED" in line:
            parts = line.strip().split()
            for part in parts:
                if "::" in part and "test_" in part:
                    func_name = part.split("::")[-1]
                    failing.append(func_name)
                    break
    return failing


def _mark_legacy_issues(
    test_output: str,
    debug_reports: list[DebugReport],
    attempts: list[str],
    legacy_issues: list[LegacyIssue],
) -> None:
    """Mark remaining test failures as legacy issues."""
    failing = _extract_failing_test_names(test_output)
    all_hypotheses: list[DebugHypothesis] = []
    for report in debug_reports:
        all_hypotheses.extend(report.hypotheses)
        all_hypotheses.extend(report.excluded_hypotheses)

    for test_name in failing:
        issue = mark_as_legacy(
            test_name=test_name,
            description=f"Test {test_name} failed after all retry attempts",
            attempts=attempts,
            hypotheses=all_hypotheses,
            recommendation="Requires manual investigation",
        )
        legacy_issues.append(issue)
