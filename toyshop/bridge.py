"""JSON-RPC bridge for subprocess communication.

Bridge positioning in architecture:
- This module is a transport adapter (stdin/stdout JSON-RPC)
- It should call orchestration facades (pm/workflows), not contain business logic
- It remains thin and dependency-injectable for testing/replacement
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from toyshop import create_llm
from toyshop.pm import run_batch


# ---------------------------------------------------------------------------
# JSON-RPC primitives
# ---------------------------------------------------------------------------


class JsonRpcError(Exception):
    """JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def make_response(result: Any, request_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "result": result, "id": request_id}


def make_error(code: int, message: str, request_id: Any, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "error": err, "id": request_id}


# ---------------------------------------------------------------------------
# Bridge service (transport-independent)
# ---------------------------------------------------------------------------


@dataclass
class BridgeService:
    """Service adapter behind JSON-RPC transport."""

    llm_factory: Callable[[], Any] = create_llm

    @staticmethod
    def _read_text_if_exists(path: Path) -> str | None:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def run_pipeline(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run full PM pipeline via facade."""
        user_input = params.get("user_input", "")
        project_name = params.get("project_name", "Untitled")
        workspace_dir = params.get("workspace_dir", "./workspace")
        project_type = params.get("project_type", "python")

        if not user_input.strip():
            raise JsonRpcError(-32602, "Missing user_input")

        ws = Path(workspace_dir)
        ws.mkdir(parents=True, exist_ok=True)

        llm = self.llm_factory()
        batch = run_batch(
            pm_root=ws / ".toyshop" / "projects",
            project_name=project_name,
            user_input=user_input,
            llm=llm,
            project_type=project_type,
        )

        openspec_dir = batch.batch_dir / "openspec"
        return {
            "current_stage": "done" if batch.status == "completed" else batch.status,
            "error": batch.error,
            "project_id": None,
            "snapshot_id": None,
            "batch_id": batch.batch_id,
            "batch_dir": str(batch.batch_dir),
            "proposal": self._read_text_if_exists(openspec_dir / "proposal.md"),
            "design": self._read_text_if_exists(openspec_dir / "design.md"),
            "tasks": self._read_text_if_exists(openspec_dir / "tasks.md"),
            "spec": self._read_text_if_exists(openspec_dir / "spec.md"),
        }

    def run_requirement(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run only requirement workflow."""
        from toyshop.workflows import run_requirement_workflow

        user_input = params.get("user_input", "")
        project_name = params.get("project_name", "Untitled")
        if not user_input.strip():
            raise JsonRpcError(-32602, "Missing user_input")

        llm = self.llm_factory()
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

    def run_architecture(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run only architecture workflow."""
        from toyshop.workflows import run_architecture_workflow
        from toyshop.openspec.parser import parse_proposal

        proposal_md = params.get("proposal_markdown", "")
        if not proposal_md:
            raise JsonRpcError(-32602, "Missing proposal_markdown")

        proposal = parse_proposal(proposal_md)
        if not proposal:
            raise JsonRpcError(-32602, "Failed to parse proposal")

        llm = self.llm_factory()
        state = run_architecture_workflow(llm=llm, proposal=proposal)
        return {
            "current_step": state.current_step,
            "error": state.error,
            "design": state.design_markdown,
            "tasks": state.tasks_markdown,
            "spec": state.spec_markdown,
        }

    def validate_openspec(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate openspec documents by type."""
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


SERVICE = BridgeService()

METHODS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "run_pipeline": SERVICE.run_pipeline,
    "run_requirement": SERVICE.run_requirement,
    "run_architecture": SERVICE.run_architecture,
    "validate_openspec": SERVICE.validate_openspec,
}


# ---------------------------------------------------------------------------
# Server loop
# ---------------------------------------------------------------------------


def process_request(request: dict[str, Any]) -> dict[str, Any]:
    request_id = request.get("id")

    if request.get("jsonrpc") != "2.0":
        return make_error(-32600, "Invalid Request", request_id)

    method = request.get("method")
    if not method:
        return make_error(-32600, "Missing method", request_id)

    if method not in METHODS:
        return make_error(-32601, f"Method not found: {method}", request_id)

    params = request.get("params", {})
    try:
        result = METHODS[method](params)
        return make_response(result, request_id)
    except JsonRpcError as e:
        return make_error(e.code, e.message, request_id, e.data)
    except Exception as e:
        return make_error(-32603, f"Internal error: {e}", request_id)


def main() -> None:
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

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
