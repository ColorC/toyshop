"""Tests for Phase 1: project type system, language support, test runner, prompt context."""

import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# ProjectType + Registry
# ---------------------------------------------------------------------------

from toyshop.project_type import (
    ProjectArtifacts,
    ProjectType,
    get_project_type,
    list_project_types,
    register_project_type,
)


class TestProjectArtifacts:
    """Tests for ProjectArtifacts validation."""

    def test_valid_artifacts(self, tmp_path):
        a = ProjectArtifacts(src=["src/"], test=["tests/"])
        errors = a.validate(tmp_path)
        assert errors == []

    def test_empty_src_fails(self, tmp_path):
        a = ProjectArtifacts(src=[], test=["tests/"])
        errors = a.validate(tmp_path)
        assert any("src" in e for e in errors)

    def test_empty_test_fails(self, tmp_path):
        a = ProjectArtifacts(src=["src/"], test=[])
        errors = a.validate(tmp_path)
        assert any("test" in e for e in errors)

    def test_nested_paths_fail(self, tmp_path):
        a = ProjectArtifacts(src=["src/"], test=["src/tests/"])
        errors = a.validate(tmp_path)
        assert any("nest" in e.lower() for e in errors)

    def test_non_nested_multiple_src(self, tmp_path):
        a = ProjectArtifacts(src=["src/main/java/", "src/client/java/"], test=["src/test/java/"])
        errors = a.validate(tmp_path)
        # These share "src/" prefix but are not nested (src/main/java/ doesn't start with src/client/java/)
        nesting_errors = [e for e in errors if "nest" in e.lower()]
        assert len(nesting_errors) == 0


class TestProjectTypeRegistry:
    """Tests for project type registration and lookup."""

    def test_builtin_python(self):
        pt = get_project_type("python")
        assert pt.language == "python"
        assert pt.test_framework == "pytest"
        assert pt.build_command is None

    def test_builtin_java(self):
        pt = get_project_type("java")
        assert pt.language == "java"
        assert pt.test_framework == "junit"
        assert pt.build_command == "./gradlew build"

    def test_builtin_java_minecraft(self):
        pt = get_project_type("java-minecraft")
        assert pt.language == "java"
        assert pt.test_framework == "rcon"

    def test_builtin_json_minecraft(self):
        pt = get_project_type("json-minecraft")
        assert pt.language == "json"
        assert pt.test_framework == "json-schema"

    def test_list_project_types(self):
        types = list_project_types()
        ids = {t.id for t in types}
        assert ids >= {"python", "java", "java-minecraft", "json-minecraft"}

    def test_unknown_type_raises(self):
        with pytest.raises(KeyError, match="Unknown project type"):
            get_project_type("nonexistent")

    def test_register_custom_type(self):
        custom = ProjectType(
            id="custom-test",
            language="python",
            display_name="Custom Test",
            default_artifacts=ProjectArtifacts(src=["lib/"], test=["spec/"]),
        )
        register_project_type(custom)
        fetched = get_project_type("custom-test")
        assert fetched.display_name == "Custom Test"


# ---------------------------------------------------------------------------
# LanguageSupport — Python
# ---------------------------------------------------------------------------

from toyshop.lang.python_lang import PythonLanguageSupport


class TestPythonLanguageSupport:
    """Tests for PythonLanguageSupport — extracted from tdd_pipeline."""

    @pytest.fixture
    def lang(self):
        return PythonLanguageSupport()

    # -- normalize_signature --

    def test_normalize_already_valid_def(self, lang):
        assert lang.normalize_signature("add", "def add(a, b)") == "def add(a, b)"

    def test_normalize_already_valid_class(self, lang):
        assert lang.normalize_signature("Calc", "class Calc:") == "class Calc:"

    def test_normalize_bare_parens(self, lang):
        result = lang.normalize_signature("add", "(a: float, b: float) -> float")
        assert result == "def add(a: float, b: float) -> float"

    def test_normalize_decorator(self, lang):
        result = lang.normalize_signature("Config", "@dataclass")
        assert result == "class Config"

    def test_normalize_type_annotation(self, lang):
        result = lang.normalize_signature("name", "name: str")
        assert result == "class name"

    # -- is_valid_signature --

    def test_valid_python_def(self, lang):
        assert lang.is_valid_signature("def add(a: int, b: int) -> int") is True

    def test_valid_python_class(self, lang):
        assert lang.is_valid_signature("class Calculator:") is True

    def test_invalid_typescript(self, lang):
        assert lang.is_valid_signature("interface Calculator extends Base") is False

    def test_invalid_ts_arrow(self, lang):
        assert lang.is_valid_signature("(a: number) => string") is False

    # -- generate_stub_for_module --

    def test_stub_simple_class(self, lang):
        ifaces = [
            {"name": "Calculator", "signature": "class Calculator:"},
            {"name": "add", "signature": "def add(self, a: float, b: float) -> float"},
        ]
        code = lang.generate_stub_for_module(ifaces)
        assert "class Calculator:" in code
        assert "def add(self, a: float, b: float) -> float:" in code
        assert "NotImplementedError" in code

    def test_stub_standalone_function(self, lang):
        ifaces = [{"name": "parse", "signature": "def parse(text: str) -> dict"}]
        code = lang.generate_stub_for_module(ifaces)
        assert "def parse(text: str) -> dict:" in code

    # -- build_module_map --

    def test_module_map_format_a(self, lang):
        modules = [{"name": "mdtable.parser", "filePath": "mdtable/parser.py"}]
        mapping = lang.build_module_map(modules)
        assert mapping["parser"] == "mdtable.parser"

    def test_module_map_format_b(self, lang):
        modules = [{"name": "Calculator Core (`core`)", "filePath": "calculator/core.py"}]
        mapping = lang.build_module_map(modules)
        assert mapping["core"] == "calculator.core"

    def test_module_map_init(self, lang):
        modules = [{"name": "calculator", "filePath": "calculator/__init__.py"}]
        mapping = lang.build_module_map(modules)
        assert mapping["calculator"] == "calculator"

    # -- module_path_from_file --

    def test_module_path_simple(self, lang):
        assert lang.module_path_from_file("calculator/core.py") == "calculator.core"

    def test_module_path_init(self, lang):
        assert lang.module_path_from_file("calculator/__init__.py") == "calculator"

    # -- build_smoke_command --

    def test_smoke_command(self, lang):
        cmd = lang.build_smoke_command(["calculator.core", "calculator.api"])
        assert "import calculator.core" in cmd
        assert "import calculator.api" in cmd
        assert "smoke ok" in cmd

    def test_smoke_command_empty(self, lang):
        cmd = lang.build_smoke_command([])
        assert "no stubs" in cmd

    # -- extract_test_metadata --

    def test_extract_test_metadata(self, lang, tmp_path):
        workspace = tmp_path / "workspace"
        test_dir = workspace / "tests"
        test_dir.mkdir(parents=True)

        (test_dir / "test_core.py").write_text(
            "def test_standalone():\n    pass\n\n"
            "class TestCalc:\n"
            "    def test_add(self):\n        pass\n"
            "    def helper(self):\n        pass\n"
        )

        files, cases = lang.extract_test_metadata(workspace)
        assert files == ["tests/test_core.py"]
        assert len(cases) == 2  # standalone + add (helper is not a test)
        names = {c["name"] for c in cases}
        assert names == {"test_standalone", "test_add"}

    def test_extract_empty_workspace(self, lang, tmp_path):
        files, cases = lang.extract_test_metadata(tmp_path)
        assert files == []
        assert cases == []

    # -- generate_test_skeletons --

    def test_generate_skeletons(self, lang, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        interfaces = [
            {"name": "Calculator", "signature": "class Calculator:", "module": "core"},
            {"name": "add", "signature": "def add(self, a, b)", "module": "core"},
        ]
        module_map = {"core": "calculator.core"}
        generated = lang.generate_test_skeletons(interfaces, module_map, workspace)
        assert len(generated) == 1
        assert "test_core.py" in generated[0]
        content = (workspace / "tests" / "test_core.py").read_text()
        assert "from calculator.core import" in content
        assert "class TestCalculator:" in content

    # -- to_snake_case --

    def test_snake_case(self, lang):
        assert lang.to_snake_case("Calculator") == "calculator"
        assert lang.to_snake_case("MemoryManager") == "memory_manager"
        assert lang.to_snake_case("HTMLParser") == "html_parser"


# ---------------------------------------------------------------------------
# LanguageSupport — Registry
# ---------------------------------------------------------------------------

from toyshop.lang.base import get_language_support


class TestLanguageRegistry:
    """Tests for language support registry."""

    def test_python_registered(self):
        lang = get_language_support("python")
        assert isinstance(lang, PythonLanguageSupport)

    def test_unknown_raises(self):
        with pytest.raises(KeyError, match="No language support"):
            get_language_support("cobol")


# ---------------------------------------------------------------------------
# TestRunner — PytestRunner
# ---------------------------------------------------------------------------

from toyshop.test_runner import PytestRunner, TestRunResult


class TestPytestRunner:
    """Tests for PytestRunner output parsing."""

    @pytest.fixture
    def runner(self):
        return PytestRunner()

    def test_parse_all_passed(self, runner):
        output = "tests/test_core.py::test_add PASSED\n=== 5 passed in 0.3s ==="
        result = runner.parse_output(output)
        assert result.all_passed is True
        assert result.passed == 5
        assert result.failed == 0

    def test_parse_with_failures(self, runner):
        output = "=== 3 passed, 2 failed in 1.2s ==="
        result = runner.parse_output(output)
        assert result.all_passed is False
        assert result.passed == 3
        assert result.failed == 2

    def test_parse_with_errors(self, runner):
        output = "=== 1 passed, 1 error in 0.5s ==="
        result = runner.parse_output(output)
        assert result.all_passed is False
        assert result.errors == 1

    def test_parse_empty_output(self, runner):
        result = runner.parse_output("")
        assert result.all_passed is False
        assert result.total == 0

    def test_parse_per_test(self, runner):
        output = (
            "tests/test_calc.py::test_add PASSED\n"
            "tests/test_calc.py::test_sub FAILED\n"
            "tests/test_calc.py::TestClass::test_mul PASSED\n"
        )
        per = runner._parse_per_test_results(output)
        assert len(per) == 3
        assert per[0].status == "passed"
        assert per[1].status == "failed"
        assert per[2].test_id == "tests/test_calc.py::TestClass::test_mul"


# ---------------------------------------------------------------------------
# PromptContext
# ---------------------------------------------------------------------------

from toyshop.prompt_context import PromptContext


class TestPromptContext:
    """Tests for PromptContext construction from ProjectType."""

    def test_from_python(self):
        pt = get_project_type("python")
        ctx = PromptContext.from_project_type(pt)
        assert ctx.language == "Python"
        assert ctx.test_framework == "pytest"
        assert "pytest" in ctx.test_command
        assert ctx.source_ext == ".py"
        assert ctx.build_command is None

    def test_from_java(self):
        pt = get_project_type("java")
        ctx = PromptContext.from_project_type(pt)
        assert ctx.language == "Java"
        assert ctx.test_framework == "JUnit 5"
        assert "gradlew" in ctx.test_command
        assert ctx.source_ext == ".java"
        assert ctx.build_command == "./gradlew build"

    def test_from_json_minecraft(self):
        pt = get_project_type("json-minecraft")
        ctx = PromptContext.from_project_type(pt)
        assert ctx.language == "JSON"
        assert ctx.test_framework == "json-schema"
