"""AST-based code version generation.

Scans Python source files using the ast module to extract structural
information (classes, functions, imports) without executing code.
Used by the change pipeline to let LLM understand existing code structure.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class VersionFunction:
    name: str
    signature: str          # "def name(args) -> ret"
    decorators: list[str] = field(default_factory=list)


@dataclass
class VersionClass:
    name: str
    methods: list[str] = field(default_factory=list)   # method signatures
    bases: list[str] = field(default_factory=list)


@dataclass
class VersionModule:
    name: str
    file_path: str          # relative path
    classes: list[VersionClass] = field(default_factory=list)
    functions: list[VersionFunction] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    line_count: int = 0


@dataclass
class CodeVersion:
    project_name: str
    root_path: str
    modules: list[VersionModule] = field(default_factory=list)
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


def scan_python_file(file_path: Path, relative_to: Path | None = None) -> VersionModule:
    """Parse a single Python file and extract its structure.

    Args:
        file_path: Absolute path to the .py file
        relative_to: Base path for computing relative file_path

    Returns:
        VersionModule with classes, functions, imports
    """
    rel_path = str(file_path.relative_to(relative_to)) if relative_to else str(file_path)
    source = file_path.read_text(encoding="utf-8")
    line_count = source.count("\n") + 1

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return VersionModule(
            name=file_path.stem,
            file_path=rel_path,
            line_count=line_count,
        )

    classes: list[VersionClass] = []
    functions: list[VersionFunction] = []
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

            classes.append(VersionClass(
                name=node.name,
                methods=methods,
                bases=bases,
            ))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(VersionFunction(
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

    return VersionModule(
        name=file_path.stem,
        file_path=rel_path,
        classes=classes,
        functions=functions,
        imports=imports,
        line_count=line_count,
    )


def _matches_ignore(parts: tuple[str, ...], patterns: list[str]) -> bool:
    """Check if any path component matches an ignore pattern."""
    for pat in patterns:
        pat_clean = pat.rstrip("/")
        for part in parts:
            if fnmatch.fnmatch(part, pat_clean):
                return True
    return False


def create_code_version(
    project_dir: Path,
    project_name: str,
    *,
    ignore_patterns: list[str] | None = None,
) -> CodeVersion:
    """Scan all Python files in a directory and create a code snapshot.

    Args:
        project_dir: Root directory to scan
        project_name: Name of the project
        ignore_patterns: Directory/file patterns to skip (fnmatch style)

    Returns:
        CodeVersion with all modules
    """
    modules = []
    for py_file in sorted(project_dir.rglob("*.py")):
        # Skip __pycache__, hidden dirs, test files
        rel = py_file.relative_to(project_dir)
        parts = rel.parts
        if any(p.startswith(".") or p == "__pycache__" for p in parts):
            continue
        if ignore_patterns and _matches_ignore(parts, ignore_patterns):
            continue
        modules.append(scan_python_file(py_file, relative_to=project_dir))

    return CodeVersion(
        project_name=project_name,
        root_path=str(project_dir),
        modules=modules,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def save_code_version(snapshot: CodeVersion, output_path: Path) -> None:
    """Write snapshot to JSON file."""
    output_path.write_text(
        json.dumps(asdict(snapshot), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_code_version(path: Path) -> CodeVersion:
    """Read snapshot from JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    modules = []
    for m in data.get("modules", []):
        classes = [VersionClass(**c) for c in m.get("classes", [])]
        functions = [VersionFunction(**f) for f in m.get("functions", [])]
        modules.append(VersionModule(
            name=m["name"],
            file_path=m["file_path"],
            classes=classes,
            functions=functions,
            imports=m.get("imports", []),
            line_count=m.get("line_count", 0),
        ))
    return CodeVersion(
        project_name=data["project_name"],
        root_path=data["root_path"],
        modules=modules,
        timestamp=data.get("timestamp", ""),
    )


# ---------------------------------------------------------------------------
# Design.md parsing (migrated from tdd_pipeline.py for decoupling)
# ---------------------------------------------------------------------------


def _parse_design_interfaces(design_md: str) -> list[dict[str, str]]:
    """Parse interface signatures from design.md markdown.

    Supports multiple formats:
      Format A (hand-written / stress-test style):
        ## Module: mdtable.parser
        ### Class: `Table`
        #### Methods
        `def __init__(self, ...) -> None`
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

        if line.startswith("## Module:"):
            current_module = line.replace("## Module:", "").strip()
        elif line.startswith("### Class:"):
            cls = line.replace("### Class:", "").strip().strip("`")
            current_class = cls
            current_name = cls
            interfaces.append({"name": cls, "signature": f"class {cls}", "module": current_module})
            in_methods_section = False
        elif line.startswith("### Function:"):
            fname = line.replace("### Function:", "").strip().strip("`")
            current_name = fname
            current_class = ""
            in_methods_section = False
        elif line.startswith("#### Methods"):
            in_methods_section = True
        elif line.startswith("#### Attributes"):
            in_methods_section = False
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
        elif line.startswith("`") and line.endswith("`") and (current_name or in_methods_section):
            sig = line.strip("`").strip()
            if sig.startswith("def "):
                name = sig.split("(")[0].replace("def ", "").strip()
            elif sig.startswith("class "):
                name = sig.split("(")[0].split(":")[0].replace("class ", "").strip()
            elif sig.startswith("@"):
                i += 1
                continue
            elif current_name:
                name = current_name
            else:
                i += 1
                continue
            interfaces.append({"name": name, "signature": sig, "module": current_module})
            if not in_methods_section and not current_class:
                current_name = ""
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
            if first_line.startswith("@"):
                class_line_idx = None
                for idx, sl in enumerate(sig_lines_parsed):
                    if sl.strip().startswith("class "):
                        class_line_idx = idx
                        break
                if class_line_idx is not None:
                    class_line = sig_lines_parsed[class_line_idx].strip().rstrip(":")
                    interfaces.append({"name": current_name, "signature": class_line, "module": current_module})
                    for sl in sig_lines_parsed[class_line_idx + 1:]:
                        sl = sl.strip()
                        if not sl or sl.startswith("#"):
                            continue
                        if sl.startswith("def "):
                            method_name = sl.split("(")[0].replace("def ", "").strip()
                            interfaces.append({"name": method_name, "signature": sl, "module": current_module})
                else:
                    interfaces.append({"name": current_name, "signature": f"class {current_name}", "module": current_module})
            elif first_line.startswith("class "):
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
        if stripped.startswith("## Module:") and not stripped.startswith("## Modules"):
            if current:
                modules.append(current)
            mod_name = stripped.replace("## Module:", "").strip()
            current = {"name": mod_name, "filePath": ""}
            continue
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


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------


def diff_version_vs_design(snapshot: CodeVersion, design_md: str) -> list[str]:
    """Compare code snapshot against design.md to detect drift.

    Returns list of warning strings describing discrepancies.
    """
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


def bidirectional_drift_check(
    snapshot: CodeVersion,
    design_md: str,
) -> dict[str, list[str]]:
    """Two-way drift detection between code and design.

    Returns:
        {
            "design_only": [...],  # In design but not in code
            "code_only": [...],    # In code but not in design
        }
    """
    design_interfaces = _parse_design_interfaces(design_md)

    # Build sets from snapshot
    snap_names: set[str] = set()
    for m in snapshot.modules:
        for f in m.functions:
            snap_names.add(f.name)
        for c in m.classes:
            snap_names.add(c.name)

    # Build set from design
    design_names: set[str] = set()
    for di in design_interfaces:
        name = di.get("name", "")
        if name:
            design_names.add(name)

    return {
        "design_only": sorted(design_names - snap_names),
        "code_only": sorted(snap_names - design_names),
    }