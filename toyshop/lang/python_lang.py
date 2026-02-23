"""Python language support for ToyShop TDD pipeline.

Extracted from tdd_pipeline.py — all Python-specific signature handling,
stub generation, test skeleton generation, and metadata extraction.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from toyshop.lang.base import LanguageSupport, register_language_support


class PythonLanguageSupport(LanguageSupport):
    """Python-specific implementation of LanguageSupport."""

    # ------------------------------------------------------------------
    # Signature handling
    # ------------------------------------------------------------------

    def normalize_signature(self, name: str, sig: str) -> str:
        """Ensure signature is valid Python: prepend 'def name' if missing."""
        sig = sig.strip()
        if sig.startswith("def ") or sig.startswith("class "):
            return sig
        # Decorator line — treat as class definition
        if sig.startswith("@"):
            return f"class {name}"
        # Bare signature like "(a: float, b: float) -> float"
        if sig.startswith("("):
            return f"def {name}{sig}"
        # Type annotation like "name: str"
        if ":" in sig and "(" not in sig:
            return f"class {name}"
        # Just a type or name without parens
        return f"def {name}({sig})"

    def is_valid_signature(self, sig: str) -> bool:
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
        stripped = sig.strip()
        if stripped.startswith(("def ", "class ", "@", "(")):
            return True
        return False

    # ------------------------------------------------------------------
    # Stub generation
    # ------------------------------------------------------------------

    def generate_stub_for_module(self, ifaces: list[dict[str, str]]) -> str:
        """Generate Python stub code for a single module's interfaces."""
        lines: list[str] = [
            '"""Auto-generated stubs from design.md signatures."""',
            "",
            "from __future__ import annotations",
            "from typing import Any, Protocol",
            "from dataclasses import dataclass",
            "",
        ]

        classes: dict[str, list[dict[str, str]]] = {}
        functions: list[dict[str, str]] = []

        for iface in ifaces:
            sig = self.normalize_signature(iface["name"], iface["signature"])
            iface = {**iface, "signature": sig}
            if sig.startswith("class "):
                class_name = sig.replace("class ", "").split("(")[0].split(":")[0].strip()
                classes[class_name] = []
            elif "self" in sig:
                if classes:
                    last_class = list(classes.keys())[-1]
                    classes[last_class].append(iface)
                else:
                    functions.append(iface)
            else:
                functions.append(iface)

        for class_name, methods in classes.items():
            orig = next((i for i in ifaces if i["name"] == class_name), None)
            sig_raw = orig["signature"] if orig else ""
            if "Protocol" in sig_raw:
                lines.append(f"class {class_name}(Protocol):")
            else:
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

        for func in functions:
            sig = func["signature"]
            lines.append(f"{sig}:")
            lines.append(f'    raise NotImplementedError("TODO: implement {func["name"]}")')
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Test skeleton generation
    # ------------------------------------------------------------------

    def generate_test_skeletons(
        self,
        interfaces: list[dict[str, str]],
        module_map: dict[str, str],
        workspace: Path,
        mode: str = "create",
    ) -> list[str]:
        """Generate pytest skeleton files with correct imports and fixtures.

        Returns list of generated file paths (relative to workspace).
        """
        py_ifaces = [i for i in interfaces if self.is_valid_signature(i["signature"])]
        if not py_ifaces:
            return []

        # Group interfaces by module short_id
        by_module: dict[str, list[dict[str, str]]] = {}
        for iface in py_ifaces:
            mod_id = iface.get("module", "").strip()
            if not mod_id:
                mod_id = "_misc"
            by_module.setdefault(mod_id, []).append(iface)

        test_dir = workspace / "tests"
        test_dir.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []

        # Collect exception classes for conftest
        exc_module_id = None
        exc_import_path = None
        exc_classes: list[str] = []
        for mod_id, mod_ifaces in by_module.items():
            import_path = module_map.get(mod_id, "")
            if "exception" in mod_id.lower() or "exception" in import_path.lower():
                exc_module_id = mod_id
                exc_import_path = import_path
                for iface in mod_ifaces:
                    sig = self.normalize_signature(iface["name"], iface["signature"])
                    if sig.startswith("class "):
                        exc_classes.append(iface["name"])

        for mod_id, mod_ifaces in by_module.items():
            import_path = module_map.get(mod_id, "")
            if not import_path:
                continue

            # Separate classes and functions
            classes: dict[str, list[dict[str, str]]] = {}
            functions: list[dict[str, str]] = []
            for iface in mod_ifaces:
                sig = self.normalize_signature(iface["name"], iface["signature"])
                if sig.startswith("class "):
                    classes[iface["name"]] = []
                elif "self" in sig:
                    if classes:
                        last_cls = list(classes.keys())[-1]
                        classes[last_cls].append(iface)
                    else:
                        functions.append(iface)
                else:
                    functions.append(iface)

            class_names = list(classes.keys())
            func_names = [f["name"] for f in functions]
            all_names = class_names + func_names
            if not all_names:
                continue

            safe_mod_id = re.sub(r"[^a-zA-Z0-9_]", "_", mod_id)
            test_file = test_dir / f"test_{safe_mod_id}.py"

            if mode == "modify" and test_file.exists():
                continue

            lines: list[str] = []
            lines.append(f'"""Tests for {mod_id} module — auto-generated skeleton."""')
            lines.append("import pytest")

            lines.append(f"from {import_path} import (")
            for name in all_names:
                lines.append(f"    {name},")
            lines.append(")")

            if exc_import_path and mod_id != exc_module_id and exc_classes:
                lines.append(f"from {exc_import_path} import (")
                for exc in exc_classes:
                    lines.append(f"    {exc},")
                lines.append(")")

            lines.append("")
            lines.append("")

            for cls_name in class_names:
                fixture_name = self.to_snake_case(cls_name)
                lines.append("@pytest.fixture")
                lines.append(f"def {fixture_name}():")
                lines.append(f"    return {cls_name}()")
                lines.append("")
                lines.append("")

            for cls_name, methods in classes.items():
                fixture_name = self.to_snake_case(cls_name)
                lines.append(f"class Test{cls_name}:")
                lines.append(f'    """Tests for {cls_name}."""')
                lines.append("")
                if not methods:
                    lines.append(f"    def test_{fixture_name}_creation(self, {fixture_name}):")
                    lines.append("        # TODO: test basic creation")
                    lines.append("        pass")
                    lines.append("")
                else:
                    for method in methods:
                        m_name = method["name"]
                        if "." in m_name:
                            m_name = m_name.rsplit(".", 1)[-1]
                        if m_name.startswith("__") and m_name.endswith("__"):
                            test_name = m_name.strip("_")
                        else:
                            test_name = m_name
                        sig = self.normalize_signature(m_name, method["signature"])
                        lines.append(f"    def test_{test_name}(self, {fixture_name}):")
                        lines.append(f"        # TODO: test {sig}")
                        lines.append("        pass")
                        lines.append("")
                lines.append("")

            for func in functions:
                sig = self.normalize_signature(func["name"], func["signature"])
                lines.append(f"def test_{func['name']}():")
                lines.append(f"    # TODO: test {sig}")
                lines.append("    pass")
                lines.append("")
                lines.append("")

            test_file.write_text("\n".join(lines), encoding="utf-8")
            generated.append(str(test_file.relative_to(workspace)))

        return generated

    # ------------------------------------------------------------------
    # Smoke test
    # ------------------------------------------------------------------

    def build_smoke_command(self, stub_modules: list[str]) -> str:
        """Build a Python import smoke-test command."""
        if not stub_modules:
            return "python3 -c 'print(\"no stubs\"); print(\"smoke ok\")'"
        smoke_imports = "; ".join(f"import {m}" for m in stub_modules)
        return f"python3 -c '{smoke_imports}; print(\"smoke ok\")'"

    # ------------------------------------------------------------------
    # Test metadata extraction (from wiki.py)
    # ------------------------------------------------------------------

    def extract_test_metadata(
        self, workspace: Path,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Scan tests/ directory and extract test case metadata via Python AST."""
        test_dir = Path(workspace) / "tests"
        if not test_dir.exists():
            return [], []

        test_files: list[str] = []
        test_cases: list[dict[str, str]] = []

        for py_file in sorted(test_dir.rglob("test_*.py")):
            rel_path = str(py_file.relative_to(workspace))
            test_files.append(rel_path)

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError):
                continue

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    test_cases.append({
                        "id": f"{rel_path}::{node.name}",
                        "name": node.name,
                        "file": rel_path,
                        "class_name": "",
                    })
                elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                            test_cases.append({
                                "id": f"{rel_path}::{node.name}::{item.name}",
                                "name": item.name,
                                "file": rel_path,
                                "class_name": node.name,
                            })

        return test_files, test_cases

    # ------------------------------------------------------------------
    # Module mapping
    # ------------------------------------------------------------------

    def module_path_from_file(self, file_path: str) -> str:
        """Convert 'calculator/core.py' → 'calculator.core'."""
        import_path = file_path.replace(".py", "").replace("/", ".")
        if import_path.endswith(".__init__"):
            import_path = import_path[: -len(".__init__")]
        return import_path

    def build_module_map(self, modules: list[dict[str, str]]) -> dict[str, str]:
        """Build short_id → python_import_path mapping from parsed modules."""
        mapping: dict[str, str] = {}
        for mod in modules:
            name = mod.get("name", "")
            file_path = mod.get("filePath", "")
            m = re.search(r"`(\w[\w-]*)`", name)
            if m:
                short_id = m.group(1)
            elif "." in name:
                short_id = name.rsplit(".", 1)[-1]
            else:
                short_id = name.lower().replace(" ", "_")
            if file_path:
                mapping[short_id] = self.module_path_from_file(file_path)
        return mapping


# ---------------------------------------------------------------------------
# Auto-register
# ---------------------------------------------------------------------------

register_language_support("python", PythonLanguageSupport())
