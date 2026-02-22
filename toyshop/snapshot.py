"""AST-based code snapshot generation.

Scans Python source files using the ast module to extract structural
information (classes, functions, imports) without executing code.
Used by the change pipeline to let LLM understand existing code structure.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class SnapshotFunction:
    name: str
    signature: str          # "def name(args) -> ret"
    decorators: list[str] = field(default_factory=list)


@dataclass
class SnapshotClass:
    name: str
    methods: list[str] = field(default_factory=list)   # method signatures
    bases: list[str] = field(default_factory=list)


@dataclass
class SnapshotModule:
    name: str
    file_path: str          # relative path
    classes: list[SnapshotClass] = field(default_factory=list)
    functions: list[SnapshotFunction] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    line_count: int = 0


@dataclass
class CodeSnapshot:
    project_name: str
    root_path: str
    modules: list[SnapshotModule] = field(default_factory=list)
    timestamp: str = ""


def _unparse_annotation(node: ast.expr | None) -> str:
    """Convert an AST annotation node to string."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _extract_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Extract full function signature from AST node."""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args = node.args

    parts = []
    # positional args
    defaults_offset = len(args.args) - len(args.defaults)
    for i, arg in enumerate(args.args):
        ann = _unparse_annotation(arg.annotation)
        param = f"{arg.arg}: {ann}" if ann else arg.arg
        di = i - defaults_offset
        if di >= 0 and di < len(args.defaults):
            param += f" = {ast.unparse(args.defaults[di])}"
        parts.append(param)

    if args.vararg:
        ann = _unparse_annotation(args.vararg.annotation)
        parts.append(f"*{args.vararg.arg}: {ann}" if ann else f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")

    for i, arg in enumerate(args.kwonlyargs):
        ann = _unparse_annotation(arg.annotation)
        param = f"{arg.arg}: {ann}" if ann else arg.arg
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            param += f" = {ast.unparse(args.kw_defaults[i])}"
        parts.append(param)

    if args.kwarg:
        ann = _unparse_annotation(args.kwarg.annotation)
        parts.append(f"**{args.kwarg.arg}: {ann}" if ann else f"**{args.kwarg.arg}")

    ret = _unparse_annotation(node.returns)
    ret_str = f" -> {ret}" if ret else ""
    return f"{prefix} {node.name}({', '.join(parts)}){ret_str}"


def _extract_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    """Extract decorator names from AST node."""
    result = []
    for dec in node.decorator_list:
        try:
            result.append(f"@{ast.unparse(dec)}")
        except Exception:
            result.append("@...")
    return result


def scan_python_file(file_path: Path, relative_to: Path | None = None) -> SnapshotModule:
    """Parse a single Python file and extract its structure.

    Args:
        file_path: Absolute path to the .py file
        relative_to: Base path for computing relative file_path

    Returns:
        SnapshotModule with classes, functions, imports
    """
    rel_path = str(file_path.relative_to(relative_to)) if relative_to else str(file_path)
    source = file_path.read_text(encoding="utf-8")
    line_count = source.count("\n") + 1

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return SnapshotModule(
            name=file_path.stem,
            file_path=rel_path,
            line_count=line_count,
        )

    classes: list[SnapshotClass] = []
    functions: list[SnapshotFunction] = []
    imports: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                try:
                    bases.append(ast.unparse(base))
                except Exception:
                    bases.append("...")

            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(_extract_signature(item))

            classes.append(SnapshotClass(
                name=node.name,
                methods=methods,
                bases=bases,
            ))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(SnapshotFunction(
                name=node.name,
                signature=_extract_signature(node),
                decorators=_extract_decorators(node),
            ))

        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f"import {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.name for a in node.names)
            imports.append(f"from {module} import {names}")

    return SnapshotModule(
        name=file_path.stem,
        file_path=rel_path,
        classes=classes,
        functions=functions,
        imports=imports,
        line_count=line_count,
    )


def create_snapshot(project_dir: Path, project_name: str) -> CodeSnapshot:
    """Scan all Python files in a directory and create a code snapshot.

    Args:
        project_dir: Root directory to scan
        project_name: Name of the project

    Returns:
        CodeSnapshot with all modules
    """
    modules = []
    for py_file in sorted(project_dir.rglob("*.py")):
        # Skip __pycache__, hidden dirs, test files
        rel = py_file.relative_to(project_dir)
        parts = rel.parts
        if any(p.startswith(".") or p == "__pycache__" for p in parts):
            continue
        modules.append(scan_python_file(py_file, relative_to=project_dir))

    return CodeSnapshot(
        project_name=project_name,
        root_path=str(project_dir),
        modules=modules,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def save_snapshot(snapshot: CodeSnapshot, output_path: Path) -> None:
    """Write snapshot to JSON file."""
    output_path.write_text(
        json.dumps(asdict(snapshot), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_snapshot(path: Path) -> CodeSnapshot:
    """Read snapshot from JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    modules = []
    for m in data.get("modules", []):
        classes = [SnapshotClass(**c) for c in m.get("classes", [])]
        functions = [SnapshotFunction(**f) for f in m.get("functions", [])]
        modules.append(SnapshotModule(
            name=m["name"],
            file_path=m["file_path"],
            classes=classes,
            functions=functions,
            imports=m.get("imports", []),
            line_count=m.get("line_count", 0),
        ))
    return CodeSnapshot(
        project_name=data["project_name"],
        root_path=data["root_path"],
        modules=modules,
        timestamp=data.get("timestamp", ""),
    )


def diff_snapshot_vs_design(snapshot: CodeSnapshot, design_md: str) -> list[str]:
    """Compare code snapshot against design.md to detect drift.

    Returns list of warning strings describing discrepancies.
    """
    from toyshop.tdd_pipeline import _parse_design_modules, _parse_design_interfaces

    warnings = []
    design_modules = _parse_design_modules(design_md)
    design_interfaces = _parse_design_interfaces(design_md)

    # Build lookup from snapshot
    snap_files = {m.file_path: m for m in snapshot.modules}
    snap_funcs: set[str] = set()
    snap_classes: set[str] = set()
    for m in snapshot.modules:
        for f in m.functions:
            snap_funcs.add(f.name)
        for c in m.classes:
            snap_classes.add(c.name)

    # Check design modules exist in code
    for dm in design_modules:
        file_path = dm.get("file_path", "")
        if file_path and file_path not in snap_files:
            warnings.append(f"design.md 模块 {dm.get('name', '?')} 的文件 {file_path} 在代码中不存在")

    # Check design interfaces exist in code
    for di in design_interfaces:
        name = di.get("name", "")
        if name and name not in snap_funcs and name not in snap_classes:
            warnings.append(f"design.md 接口 {name} 在代码中未找到")

    return warnings