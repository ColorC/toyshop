"""Java language support for ToyShop TDD pipeline.

Handles Java signature normalization, stub generation (class + method stubs),
JUnit 5 test skeleton generation, and @Test annotation scanning for metadata.
"""

from __future__ import annotations

import re
from pathlib import Path

from toyshop.lang.base import LanguageSupport, register_language_support


class JavaLanguageSupport(LanguageSupport):
    """Java-specific implementation of LanguageSupport."""

    # ------------------------------------------------------------------
    # Signature handling
    # ------------------------------------------------------------------

    _JAVA_MODIFIERS = {"public", "private", "protected", "static", "final",
                       "abstract", "synchronized", "native", "default"}

    def normalize_signature(self, name: str, sig: str) -> str:
        """Normalize a raw signature into valid Java syntax.

        Handles formats from design.md:
        - Already valid: "public int add(int a, int b)"
        - Bare params: "(int a, int b) -> int"  → "public int add(int a, int b)"
        - Class-like: "class Calculator" or "Calculator"
        - Interface: "interface Foo"
        """
        sig = sig.strip().rstrip(";")

        # Already looks like a Java class/interface/enum declaration
        if re.match(r"(public\s+|abstract\s+|final\s+)*(class|interface|enum)\s+", sig):
            return sig

        # Already looks like a Java method (has modifier or return type + parens)
        if "(" in sig and self._looks_like_java_method(sig):
            return sig

        # Bare params: "(int a, int b) -> int" or "(String s)"
        if sig.startswith("("):
            return_type, params = self._parse_arrow_sig(sig)
            return f"public {return_type} {name}{params}"

        # Just a type name or annotation — treat as class
        if sig.startswith("@") or not "(" in sig:
            return f"public class {name}"

        # Fallback: wrap as method
        return f"public void {name}({sig})"

    def _looks_like_java_method(self, sig: str) -> bool:
        """Check if sig looks like a Java method declaration."""
        # Strip leading modifiers
        words = sig.split()
        if not words:
            return False
        # Has parens and at least a return type before method name
        if "(" not in sig:
            return False
        before_paren = sig[:sig.index("(")].strip()
        parts = before_paren.split()
        # Need at least return_type + method_name
        return len(parts) >= 2

    def _parse_arrow_sig(self, sig: str) -> tuple[str, str]:
        """Parse '(int a, int b) -> int' into ('int', '(int a, int b)')."""
        m = re.match(r"(\([^)]*\))\s*(?:->|→)\s*(.+)", sig)
        if m:
            return m.group(2).strip(), m.group(1)
        # No arrow — void
        m2 = re.match(r"(\([^)]*\))", sig)
        if m2:
            return "void", m2.group(1)
        return "void", "()"

    def is_valid_signature(self, sig: str) -> bool:
        """Check if a signature looks like valid Java (not Python/TS)."""
        python_markers = ["def ", "self,", "self)", "-> ", "import "]
        ts_markers = ["=> ", "readonly ", "keyof ", "Partial<", "interface ",
                      ": string", ": number", ": boolean", "?: "]
        sig_stripped = sig.strip()
        for marker in python_markers + ts_markers:
            if marker in sig_stripped:
                return False
        # Python-style "class Foo:" (with colon, no modifier) — reject
        if re.match(r"^class\s+\w+\s*:", sig_stripped):
            return False
        # Positive signals for Java
        java_signals = [
            re.search(r"\b(public|private|protected|static|final|abstract|void|int|long|double|float|boolean|String)\b", sig_stripped),
            re.search(r"\b(class|interface|enum)\s+\w+", sig_stripped),
            re.search(r"\b\w+\s+\w+\s*\(", sig_stripped),  # return_type name(
        ]
        return any(java_signals)

    # ------------------------------------------------------------------
    # Stub generation
    # ------------------------------------------------------------------

    def generate_stub_for_module(self, ifaces: list[dict[str, str]]) -> str:
        """Generate Java stub code for a single module's interfaces.

        Produces a class with method stubs that throw UnsupportedOperationException.
        """
        lines: list[str] = [
            "// Auto-generated stubs from design.md signatures.",
            "",
        ]

        classes: dict[str, list[dict[str, str]]] = {}
        functions: list[dict[str, str]] = []

        for iface in ifaces:
            sig = self.normalize_signature(iface["name"], iface["signature"])
            iface_norm = {**iface, "signature": sig}

            if re.match(r"(public\s+|abstract\s+|final\s+)*(class|interface|enum)\s+", sig):
                class_name = self._extract_class_name(sig)
                classes[class_name] = []
            elif classes:
                # Method belongs to last class
                last_class = list(classes.keys())[-1]
                classes[last_class].append(iface_norm)
            else:
                functions.append(iface_norm)

        for class_name, methods in classes.items():
            orig = next((i for i in ifaces if i["name"] == class_name), None)
            sig_raw = orig["signature"] if orig else ""

            if "interface" in sig_raw.lower():
                lines.append(f"public interface {class_name} {{")
                for method in methods:
                    lines.append(f"    {method['signature']};")
                    lines.append("")
                lines.append("}")
            else:
                lines.append(f"public class {class_name} {{")
                if not methods:
                    lines.append("    // TODO: implement")
                for method in methods:
                    sig = method["signature"]
                    lines.append(f"    {sig} {{")
                    lines.append(f'        throw new UnsupportedOperationException("TODO: implement {method["name"]}");')
                    lines.append("    }")
                    lines.append("")
                lines.append("}")
            lines.append("")

        for func in functions:
            sig = func["signature"]
            lines.append(f"    {sig} {{")
            lines.append(f'        throw new UnsupportedOperationException("TODO: implement {func["name"]}");')
            lines.append("    }")
            lines.append("")

        return "\n".join(lines)

    def _extract_class_name(self, sig: str) -> str:
        """Extract class name from 'public class Foo extends Bar'."""
        m = re.search(r"\b(?:class|interface|enum)\s+(\w+)", sig)
        return m.group(1) if m else "Unknown"

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
        """Generate JUnit 5 test skeleton files.

        Returns list of generated file paths (relative to workspace).
        """
        java_ifaces = [i for i in interfaces if self.is_valid_signature(i["signature"])]
        if not java_ifaces:
            return []

        # Group by module
        by_module: dict[str, list[dict[str, str]]] = {}
        for iface in java_ifaces:
            mod_id = iface.get("module", "").strip() or "_misc"
            by_module.setdefault(mod_id, []).append(iface)

        test_dir = workspace / "src" / "test" / "java"
        test_dir.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []

        for mod_id, mod_ifaces in by_module.items():
            import_path = module_map.get(mod_id, "")
            if not import_path:
                continue

            # Extract package and class name from import path
            package = self._package_from_import(import_path)
            # Collect class names for this module
            class_names: list[str] = []
            methods_by_class: dict[str, list[dict[str, str]]] = {}

            for iface in mod_ifaces:
                sig = self.normalize_signature(iface["name"], iface["signature"])
                if re.match(r"(public\s+|abstract\s+|final\s+)*(class|interface|enum)\s+", sig):
                    cls = self._extract_class_name(sig)
                    class_names.append(cls)
                    methods_by_class[cls] = []
                elif class_names:
                    methods_by_class[class_names[-1]].append(iface)

            if not class_names:
                continue

            for cls_name in class_names:
                test_class_name = f"{cls_name}Test"

                # Build package directory structure
                pkg_dir = test_dir
                if package:
                    pkg_dir = test_dir / package.replace(".", "/")
                    pkg_dir.mkdir(parents=True, exist_ok=True)

                test_file = pkg_dir / f"{test_class_name}.java"
                if mode == "modify" and test_file.exists():
                    continue

                lines: list[str] = []
                if package:
                    lines.append(f"package {package};")
                    lines.append("")

                lines.append("import org.junit.jupiter.api.Test;")
                lines.append("import org.junit.jupiter.api.BeforeEach;")
                lines.append("import static org.junit.jupiter.api.Assertions.*;")
                lines.append("")
                if package and import_path != package:
                    lines.append(f"import {import_path};")
                    lines.append("")

                lines.append(f"/**")
                lines.append(f" * Tests for {cls_name}.")
                lines.append(f" */")
                lines.append(f"class {test_class_name} {{")
                lines.append("")
                lines.append(f"    private {cls_name} instance;")
                lines.append("")
                lines.append("    @BeforeEach")
                lines.append("    void setUp() {")
                lines.append(f"        instance = new {cls_name}();")
                lines.append("    }")
                lines.append("")

                methods = methods_by_class.get(cls_name, [])
                if not methods:
                    lines.append("    @Test")
                    lines.append(f"    void testCreation() {{")
                    lines.append(f"        assertNotNull(instance);")
                    lines.append("    }")
                else:
                    for method in methods:
                        m_name = method["name"]
                        if "." in m_name:
                            m_name = m_name.rsplit(".", 1)[-1]
                        test_name = "test" + m_name[0].upper() + m_name[1:]
                        lines.append("    @Test")
                        lines.append(f"    void {test_name}() {{")
                        lines.append(f"        // TODO: test {m_name}")
                        lines.append(f"        fail(\"Not yet implemented\");")
                        lines.append("    }")
                        lines.append("")

                lines.append("}")
                lines.append("")

                test_file.write_text("\n".join(lines), encoding="utf-8")
                generated.append(str(test_file.relative_to(workspace)))

        return generated

    def _package_from_import(self, import_path: str) -> str:
        """Extract package from 'com.example.Calculator' → 'com.example'."""
        if "." in import_path:
            return import_path.rsplit(".", 1)[0]
        return ""

    # ------------------------------------------------------------------
    # Smoke test
    # ------------------------------------------------------------------

    def build_smoke_command(self, stub_modules: list[str]) -> str:
        """Build a Gradle compile command as smoke test.

        For Java, compilation IS the smoke test — if it compiles, stubs are valid.
        """
        if not stub_modules:
            return "echo 'no stubs to compile' && echo 'smoke ok'"
        return "./gradlew compileJava 2>&1 && echo 'smoke ok'"

    # ------------------------------------------------------------------
    # Test metadata extraction
    # ------------------------------------------------------------------

    def extract_test_metadata(
        self, workspace: Path,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Scan test directories for @Test annotations via regex."""
        test_dirs = [
            workspace / "src" / "test" / "java",
            workspace / "tests",
        ]

        test_files: list[str] = []
        test_cases: list[dict[str, str]] = []

        for test_dir in test_dirs:
            if not test_dir.exists():
                continue

            for java_file in sorted(test_dir.rglob("*Test.java")):
                rel_path = str(java_file.relative_to(workspace))
                test_files.append(rel_path)

                try:
                    source = java_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                # Extract class name
                class_match = re.search(r"class\s+(\w+)", source)
                class_name = class_match.group(1) if class_match else ""

                # Extract package
                pkg_match = re.search(r"package\s+([\w.]+)\s*;", source)
                package = pkg_match.group(1) if pkg_match else ""

                # Find @Test methods
                for m in re.finditer(r"@Test\s+(?:void|public\s+void)\s+(\w+)\s*\(", source):
                    method_name = m.group(1)
                    fqn = f"{package}.{class_name}" if package else class_name
                    test_cases.append({
                        "id": f"{fqn}#{method_name}",
                        "name": method_name,
                        "file": rel_path,
                        "class_name": class_name,
                    })

        return test_files, test_cases

    # ------------------------------------------------------------------
    # Module mapping
    # ------------------------------------------------------------------

    def module_path_from_file(self, file_path: str) -> str:
        """Convert 'com/example/Calculator.java' → 'com.example.Calculator'."""
        # Strip common prefixes
        for prefix in ("src/main/java/", "src/client/java/"):
            if file_path.startswith(prefix):
                file_path = file_path[len(prefix):]
                break
        import_path = file_path.replace(".java", "").replace("/", ".")
        return import_path

    def build_module_map(self, modules: list[dict[str, str]]) -> dict[str, str]:
        """Build short_id → Java import path mapping from parsed modules."""
        mapping: dict[str, str] = {}
        for mod in modules:
            name = mod.get("name", "")
            file_path = mod.get("filePath", "")

            # Extract short_id from backtick notation or last segment
            m = re.search(r"`(\w[\w-]*)`", name)
            if m:
                short_id = m.group(1)
            elif "." in name:
                short_id = name.rsplit(".", 1)[-1]
            else:
                short_id = name.strip()

            if file_path:
                mapping[short_id] = self.module_path_from_file(file_path)
        return mapping


# ---------------------------------------------------------------------------
# Auto-register
# ---------------------------------------------------------------------------

register_language_support("java", JavaLanguageSupport())
