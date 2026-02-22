"""Coding Agent - Generates and tests code based on design documents.

This module provides a Coding Agent that uses OpenHands tools to:
1. Read design documents (proposal, design, tasks, spec)
2. Generate code implementations
3. Run tests to validate the implementation
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import Conversation

# Import OpenHands tools to trigger registration
# These imports must happen before creating the Agent
import openhands.tools.file_editor  # noqa: F401 - registers "file_editor"
import openhands.tools.terminal  # noqa: F401 - registers "terminal"
import openhands.tools.glob  # noqa: F401 - registers "glob"
import openhands.tools.grep  # noqa: F401 - registers "grep"


# System prompt for the Coding Agent - Create mode (greenfield)
CODING_AGENT_CREATE_PROMPT = """You are an expert software developer. Your task is to implement code based on design documents.

## Your Workflow

### Phase 1: Read Design Documents
1. Read `openspec/proposal.md` to understand requirements and goals
2. Read `openspec/design.md` to understand architecture and modules
3. Read `openspec/tasks.md` to understand implementation tasks
4. Read `openspec/spec.md` to understand test scenarios

### Phase 2: Implement Code
1. Create the project structure with required directories
2. Create `requirements.txt` or `pyproject.toml` with dependencies
3. Implement each module according to the design
4. Follow the task breakdown order
5. Ensure all interfaces match the design specifications

### Phase 3: Test Implementation
1. Create test files based on spec.md test scenarios
2. Run tests using pytest or appropriate test framework
3. Fix any failing tests
4. Ensure all tests pass

## Important Guidelines
- Write clean, well-documented code
- Follow Python best practices (PEP 8)
- Include type hints
- Handle errors gracefully

## Available Tools
- Use terminal to run commands (pip install, pytest, etc.)
- Use file editor to create and modify files
- Use glob/grep to explore the codebase

When complete, summarize what was implemented and the test results.
"""

# System prompt for the Coding Agent - Modify mode (brownfield)
CODING_AGENT_MODIFY_PROMPT = """You are an expert software developer. Your task is to modify an existing codebase based on change requirements and design documents.

## Context

You are working on an existing project. The current architecture and OpenSpec documents are provided below.
Your job is to understand the existing code, locate the files that need changes, and apply modifications precisely.

## Your Workflow

### Phase 1: Understand Existing Code
1. Read the architecture context provided below to understand the current structure
2. Read `openspec/design.md` for the change design
3. Read `openspec/tasks.md` for the change tasks
4. Use glob/grep to explore the actual codebase and verify the architecture

### Phase 2: Locate and Modify
1. For each task, identify the exact files and locations that need changes
2. Read the target files to understand the current implementation
3. Apply modifications precisely — edit existing files, don't recreate them
4. Create new files only when the design explicitly requires them
5. Ensure modifications are consistent with the existing code style and patterns

### Phase 3: Verify Changes
1. Run existing tests to ensure no regressions
2. Add new tests for the changed functionality
3. Fix any failing tests
4. Ensure all tests pass (both old and new)

## Important Guidelines
- NEVER rewrite files from scratch — make targeted edits
- Preserve existing code style, naming conventions, and patterns
- Keep imports organized consistently with the existing code
- When adding new modules, follow the same structure as existing ones
- Run tests after each significant change to catch regressions early

## Available Tools
- Use terminal to run commands (pip install, pytest, etc.)
- Use file editor to view and modify files
- Use glob/grep to explore and search the codebase

When complete, summarize:
- Files modified (with description of changes)
- Files created (if any)
- Test results (existing + new)
"""


@dataclass
class CodingResult:
    """Result of coding agent execution."""
    success: bool
    files_created: list[str]
    files_modified: list[str]
    test_results: str
    summary: str
    iterations: int


def create_coding_agent(llm: LLM, mode: str = "create", architecture_context: str = "") -> Agent:
    """Create a coding agent with file editing and terminal tools.

    Args:
        llm: LLM instance
        mode: "create" for greenfield, "modify" for brownfield
        architecture_context: Existing architecture info (used in modify mode)

    Returns:
        Configured Agent for code generation
    """
    if mode == "modify":
        prompt = CODING_AGENT_MODIFY_PROMPT
        if architecture_context:
            prompt += f"\n## Current Architecture\n\n{architecture_context}\n"
    else:
        prompt = CODING_AGENT_CREATE_PROMPT

    agent = Agent(
        llm=llm,
        tools=[
            # File operations (snake_case names as registered)
            {"name": "file_editor"},
            # Terminal for running commands
            {"name": "terminal"},
            # Search utilities
            {"name": "glob"},
            {"name": "grep"},
        ],
        include_default_tools=["FinishTool"],
        system_prompt_kwargs={
            "custom_prompt": prompt,
        },
    )

    return agent


def _load_architecture_context(workspace: Path, project_id: str | None = None) -> str:
    """Load architecture context from storage and openspec documents.

    Args:
        workspace: Project workspace directory
        project_id: Optional project ID to load from database

    Returns:
        Formatted architecture context string
    """
    sections = []

    # 1. Load from database snapshot if project_id provided
    if project_id:
        try:
            from toyshop.storage.database import init_database, get_latest_snapshot, get_project, close_database

            db_path = workspace / ".toyshop" / "architecture.db"
            if db_path.exists():
                init_database(db_path)
                snapshot = get_latest_snapshot(project_id)
                if snapshot:
                    modules = snapshot.get("modules", [])
                    interfaces = snapshot.get("interfaces", [])
                    sections.append("### Stored Architecture Snapshot")
                    sections.append(f"Project ID: {project_id}")
                    sections.append(f"Version: {snapshot.get('version', 'unknown')}")
                    sections.append(f"Modules: {len(modules)}")
                    for mod in modules:
                        name = mod.get("name", "")
                        desc = mod.get("description", "")
                        path = mod.get("filePath", mod.get("file_path", ""))
                        sections.append(f"  - {name}: {desc} ({path})")
                    sections.append(f"Interfaces: {len(interfaces)}")
                    for iface in interfaces:
                        name = iface.get("name", "")
                        sig = iface.get("signature", "")
                        sections.append(f"  - {name}: {sig}")
                close_database()
        except Exception:
            pass  # Database not available, fall through to file-based

    # 2. Load existing openspec documents
    openspec_dir = workspace / "openspec"
    if openspec_dir.exists():
        for doc_name in ["proposal.md", "design.md", "tasks.md", "spec.md"]:
            doc_path = openspec_dir / doc_name
            if doc_path.exists():
                content = doc_path.read_text(encoding="utf-8")
                if content.strip():
                    sections.append(f"### {doc_name}")
                    # Truncate very long documents
                    if len(content) > 3000:
                        content = content[:3000] + "\n... (truncated)"
                    sections.append(content)

    # 3. Scan existing code structure
    code_files = []
    for f in workspace.rglob("*.py"):
        rel = f.relative_to(workspace)
        if not str(rel).startswith((".", "openspec", "__pycache__", ".toyshop")):
            code_files.append(str(rel))

    if code_files:
        sections.append("### Existing Code Files")
        for cf in sorted(code_files):
            sections.append(f"  - {cf}")

    return "\n\n".join(sections)


def run_coding_workflow(
    workspace: str | Path,
    llm: LLM | None = None,
    max_iterations: int = 100,
    language: str = "python",
    mode: str = "create",
    project_id: str | None = None,
    change_request: str | None = None,
) -> CodingResult:
    """Run the coding workflow to generate and test code.

    Args:
        workspace: Directory containing openspec/ design documents
        llm: LLM instance (created from config if not provided)
        max_iterations: Maximum agent iterations
        language: Target language (python, typescript, etc.)
        mode: "create" for new project, "modify" for existing project
        project_id: Project ID for loading architecture (modify mode)
        change_request: Description of changes to make (modify mode)

    Returns:
        CodingResult with implementation details
    """
    from toyshop import create_toyshop_llm

    if llm is None:
        llm = create_toyshop_llm()

    workspace = Path(workspace)

    # Load architecture context for modify mode
    architecture_context = ""
    if mode == "modify":
        architecture_context = _load_architecture_context(workspace, project_id)

    agent = create_coding_agent(llm, mode=mode, architecture_context=architecture_context)

    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
    )

    # Build prompt based on mode
    if mode == "modify":
        prompt = f"""Please modify the existing codebase based on the change requirements.

Target Language: {language}
Change Request: {change_request or "See openspec/ documents for change details."}

Steps:
1. Understand the existing codebase structure
2. Read the change design documents in openspec/
3. Locate the files that need modification
4. Apply changes precisely — edit existing files, create new ones only when needed
5. Run existing tests to check for regressions
6. Add new tests for changed functionality
7. Ensure all tests pass

When complete, provide a summary of:
- Files modified (with description of changes)
- Files created (if any)
- Test results
- Any issues encountered
"""
    else:
        prompt = f"""Please implement the code for this project based on the design documents in the openspec/ directory.

Target Language: {language}

Steps:
1. Read all design documents in openspec/ directory
2. Create the project structure
3. Implement all modules according to the design
4. Create tests based on spec.md
5. Run all tests and ensure they pass

When complete, provide a summary of:
- Files created
- Test results
- Any issues encountered
"""

    conversation.send_message(prompt)
    conversation.run()

    # Collect results
    files_created = []
    files_modified = []

    # Walk the workspace to find created files
    for f in workspace.rglob("*"):
        if f.is_file():
            rel_path = f.relative_to(workspace)
            if not str(rel_path).startswith((".toyshop", "openspec", "__pycache__", ".git")):
                files_created.append(str(rel_path))

    return CodingResult(
        success=True,
        files_created=files_created,
        files_modified=files_modified,
        test_results="See conversation history",
        summary=f"Coding workflow completed (mode={mode})",
        iterations=max_iterations,
    )


def run_full_pipeline_with_coding(
    user_input: str,
    project_name: str,
    workspace: str | Path,
    llm: LLM | None = None,
    language: str = "python",
    run_tests: bool = True,
    tdd_mode: bool = False,
) -> dict[str, Any]:
    """Run the complete pipeline: design → code → test.

    Args:
        user_input: User requirements description
        project_name: Name of the project
        workspace: Directory for all outputs
        llm: LLM instance
        language: Target language
        run_tests: Whether to run tests
        tdd_mode: If True, use TDD pipeline (test-first) instead of standard coding

    Returns:
        Dictionary with all pipeline results
    """
    from toyshop import run_toyshop_workflow, run_ux_evaluation, UxEvaluationMode

    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    results = {
        "workspace": str(workspace),
        "project_name": project_name,
    }

    # Phase 1: Design
    print("=" * 60)
    print("Phase 1: Design")
    print("=" * 60)

    conversation = run_toyshop_workflow(
        user_input=user_input,
        project_name=project_name,
        workspace=workspace,
        llm=llm,
        persist=True,
    )

    results["design"] = {
        "proposal": conversation.get_proposal(),
        "design": conversation.get_design(),
        "tasks": conversation.get_tasks(),
        "spec": conversation.get_spec(),
        "project_id": conversation.project_id,
    }

    # Phase 2: Coding
    print("\n" + "=" * 60)
    print(f"Phase 2: {'TDD Pipeline' if tdd_mode else 'Coding'}")
    print("=" * 60)

    if tdd_mode:
        from toyshop.tdd_pipeline import run_tdd_pipeline
        tdd_result = run_tdd_pipeline(
            workspace=workspace,
            llm=llm,
            language=language,
        )
        results["coding"] = {
            "files_created": tdd_result.files_created,
            "test_files": tdd_result.test_files,
            "whitebox_passed": tdd_result.whitebox_passed,
            "blackbox_passed": tdd_result.blackbox_passed,
            "summary": tdd_result.summary,
            "retry_count": tdd_result.retry_count,
        }
    else:
        coding_result = run_coding_workflow(
            workspace=workspace,
            llm=llm,
            language=language,
        )
        results["coding"] = {
            "files_created": coding_result.files_created,
            "summary": coding_result.summary,
        }

    # Phase 3: UX Evaluation (optional)
    if run_tests:
        print("\n" + "=" * 60)
        print("Phase 3: UX Evaluation")
        print("=" * 60)

        ux_result = run_ux_evaluation(
            target_workspace=workspace,
            task_description=f"评估 {project_name} 项目的实现质量",
            llm=llm,
            mode=UxEvaluationMode.E2E,
        )

        results["ux_evaluation"] = {
            "assessment_level": ux_result.assessment_level,
            "summary": ux_result.summary,
            "report": ux_result.report,
        }

    return results
