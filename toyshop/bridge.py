"""JSON-RPC bridge for subprocess communication.

This module provides stdin/stdout based JSON-RPC server that can be
called from TypeScript via child_process.

Usage:
    python -m toyshop.bridge

Protocol:
    Request:  {"jsonrpc":"2.0","method":"run_pipeline","params":{...},"id":1}
    Response: {"jsonrpc":"2.0","result":{...},"id":1}
    Error:    {"jsonrpc":"2.0","error":{"code":...,"message":...},"id":1}
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from toyshop import create_llm, run_development_pipeline


# ---------------------------------------------------------------------------
# JSON-RPC Implementation
# ---------------------------------------------------------------------------


class JsonRpcError(Exception):
    """JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def make_response(result: Any, request_id: Any) -> dict:
    """Create a successful response."""
    return {"jsonrpc": "2.0", "result": result, "id": request_id}


def make_error(code: int, message: str, request_id: Any, data: Any = None) -> dict:
    """Create an error response."""
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "error": error, "id": request_id}


# ---------------------------------------------------------------------------
# Method Handlers
# ---------------------------------------------------------------------------


def handle_run_pipeline(params: dict) -> dict:
    """Run the complete development pipeline."""
    user_input = params.get("user_input", "")
    project_name = params.get("project_name", "Untitled")
    workspace_dir = params.get("workspace_dir", "./workspace")

    llm = create_llm()
    state = run_development_pipeline(
        user_input=user_input,
        project_name=project_name,
        workspace_dir=workspace_dir,
        llm=llm,
    )

    return {
        "current_stage": state.current_stage,
        "error": state.error,
        "project_id": state.project_id,
        "snapshot_id": state.snapshot_id,
        "proposal": state.requirement.proposal_markdown if state.requirement else None,
        "design": state.architecture.design_markdown if state.architecture else None,
        "tasks": state.architecture.tasks_markdown if state.architecture else None,
        "spec": state.architecture.spec_markdown if state.architecture else None,
    }


def handle_run_requirement(params: dict) -> dict:
    """Run only the requirement stage."""
    from toyshop.workflows import run_requirement_workflow

    user_input = params.get("user_input", "")
    project_name = params.get("project_name", "Untitled")

    llm = create_llm()
    state = run_requirement_workflow(
        llm=llm,
        user_input=user_input,
        project_name=project_name,
    )

    return {
        "current_step": state.current_step,
        "error": state.error,
        "proposal": state.proposal_markdown,
    }


def handle_run_architecture(params: dict) -> dict:
    """Run only the architecture stage."""
    from toyshop.workflows import run_architecture_workflow
    from toyshop.openspec.parser import parse_proposal

    proposal_md = params.get("proposal_markdown", "")
    if not proposal_md:
        raise JsonRpcError(-32602, "Missing proposal_markdown")

    proposal = parse_proposal(proposal_md)
    if not proposal:
        raise JsonRpcError(-32602, "Failed to parse proposal")

    llm = create_llm()
    state = run_architecture_workflow(llm=llm, proposal=proposal)

    return {
        "current_step": state.current_step,
        "error": state.error,
        "design": state.design_markdown,
        "tasks": state.tasks_markdown,
        "spec": state.spec_markdown,
    }


def handle_validate_openspec(params: dict) -> dict:
    """Validate OpenSpec documents."""
    from toyshop.openspec.parser import parse_proposal, parse_design, parse_tasks, parse_spec
    from toyshop.openspec.validator import validate_proposal, validate_design, validate_tasks, validate_spec

    doc_type = params.get("type", "proposal")
    content = params.get("content", "")

    if doc_type == "proposal":
        doc = parse_proposal(content)
        if not doc:
            return {"valid": False, "errors": [{"path": "", "message": "Failed to parse"}]}
        result = validate_proposal(doc)
    elif doc_type == "design":
        doc = parse_design(content)
        if not doc:
            return {"valid": False, "errors": [{"path": "", "message": "Failed to parse"}]}
        result = validate_design(doc)
    elif doc_type == "tasks":
        doc = parse_tasks(content)
        if not doc:
            return {"valid": False, "errors": [{"path": "", "message": "Failed to parse"}]}
        result = validate_tasks(doc)
    elif doc_type == "spec":
        doc = parse_spec(content)
        if not doc:
            return {"valid": False, "errors": [{"path": "", "message": "Failed to parse"}]}
        result = validate_spec(doc)
    else:
        raise JsonRpcError(-32602, f"Unknown document type: {doc_type}")

    return {
        "valid": result.valid,
        "errors": [{"path": e.path, "message": e.message} for e in result.errors],
        "warnings": [{"path": w.path, "message": w.message} for w in result.warnings],
    }


# Method registry
METHODS: dict[str, Callable[[dict], dict]] = {
    "run_pipeline": handle_run_pipeline,
    "run_requirement": handle_run_requirement,
    "run_architecture": handle_run_architecture,
    "validate_openspec": handle_validate_openspec,
}


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def process_request(request: dict) -> dict:
    """Process a single JSON-RPC request."""
    request_id = request.get("id")

    # Validate request
    if request.get("jsonrpc") != "2.0":
        return make_error(-32600, "Invalid Request", request_id)

    method = request.get("method")
    if not method:
        return make_error(-32600, "Missing method", request_id)

    if method not in METHODS:
        return make_error(-32601, f"Method not found: {method}", request_id)

    params = request.get("params", {})

    # Execute method
    try:
        result = METHODS[method](params)
        return make_response(result, request_id)
    except JsonRpcError as e:
        return make_error(e.code, e.message, request_id, e.data)
    except Exception as e:
        return make_error(-32603, f"Internal error: {e}", request_id)


def main():
    """Main entry point for the bridge server."""
    # Read from stdin, write to stdout
    # Log to stderr (doesn't interfere with JSON-RPC)
    print("[OK] ToyShop bridge started", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            response = make_error(-32700, f"Parse error: {e}", None)
        else:
            response = process_request(request)

        # Write response as single line
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
