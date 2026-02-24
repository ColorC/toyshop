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


# ---------------------------------------------------------------------------
# LanguageSupport — Java
# ---------------------------------------------------------------------------

from toyshop.lang.java_lang import JavaLanguageSupport


class TestJavaLanguageSupport:
    """Tests for JavaLanguageSupport."""

    @pytest.fixture
    def lang(self):
        return JavaLanguageSupport()

    # -- normalize_signature --

    def test_normalize_already_valid_class(self, lang):
        assert lang.normalize_signature("Calc", "public class Calc") == "public class Calc"

    def test_normalize_already_valid_method(self, lang):
        sig = "public int add(int a, int b)"
        assert lang.normalize_signature("add", sig) == sig

    def test_normalize_interface(self, lang):
        sig = "public interface Sortable"
        assert lang.normalize_signature("Sortable", sig) == sig

    def test_normalize_bare_params_with_arrow(self, lang):
        result = lang.normalize_signature("add", "(int a, int b) -> int")
        assert result == "public int add(int a, int b)"

    def test_normalize_bare_params_void(self, lang):
        result = lang.normalize_signature("reset", "()")
        assert result == "public void reset()"

    def test_normalize_annotation_as_class(self, lang):
        result = lang.normalize_signature("Config", "@Data")
        assert result == "public class Config"

    def test_normalize_plain_name_as_class(self, lang):
        result = lang.normalize_signature("Calculator", "Calculator")
        assert result == "public class Calculator"

    # -- is_valid_signature --

    def test_valid_java_method(self, lang):
        assert lang.is_valid_signature("public int add(int a, int b)") is True

    def test_valid_java_class(self, lang):
        assert lang.is_valid_signature("public class Calculator") is True

    def test_valid_java_void(self, lang):
        assert lang.is_valid_signature("void reset()") is True

    def test_invalid_python_def(self, lang):
        assert lang.is_valid_signature("def add(self, a, b)") is False

    def test_invalid_ts_arrow(self, lang):
        assert lang.is_valid_signature("(a: number) => string") is False

    # -- generate_stub_for_module --

    def test_stub_simple_class(self, lang):
        ifaces = [
            {"name": "Calculator", "signature": "public class Calculator"},
            {"name": "add", "signature": "public int add(int a, int b)"},
        ]
        code = lang.generate_stub_for_module(ifaces)
        assert "public class Calculator {" in code
        assert "public int add(int a, int b) {" in code
        assert "UnsupportedOperationException" in code

    def test_stub_interface(self, lang):
        ifaces = [
            {"name": "Sortable", "signature": "public interface Sortable"},
            {"name": "sort", "signature": "public void sort(int[] arr)"},
        ]
        code = lang.generate_stub_for_module(ifaces)
        assert "public interface Sortable {" in code
        assert "public void sort(int[] arr);" in code

    # -- build_module_map --

    def test_module_map_java(self, lang):
        modules = [{"name": "Calculator (`Calculator`)", "filePath": "src/main/java/com/example/Calculator.java"}]
        mapping = lang.build_module_map(modules)
        assert mapping["Calculator"] == "com.example.Calculator"

    def test_module_map_strips_prefix(self, lang):
        modules = [{"name": "core", "filePath": "src/main/java/com/app/Core.java"}]
        mapping = lang.build_module_map(modules)
        assert mapping["core"] == "com.app.Core"

    # -- module_path_from_file --

    def test_module_path_java(self, lang):
        assert lang.module_path_from_file("src/main/java/com/example/Calc.java") == "com.example.Calc"

    def test_module_path_no_prefix(self, lang):
        assert lang.module_path_from_file("com/example/Calc.java") == "com.example.Calc"

    # -- build_smoke_command --

    def test_smoke_command(self, lang):
        cmd = lang.build_smoke_command(["com.example.Calc"])
        assert "gradlew" in cmd
        assert "smoke ok" in cmd

    def test_smoke_command_empty(self, lang):
        cmd = lang.build_smoke_command([])
        assert "no stubs" in cmd
        assert "smoke ok" in cmd

    # -- extract_test_metadata --

    def test_extract_test_metadata(self, lang, tmp_path):
        workspace = tmp_path / "workspace"
        test_dir = workspace / "src" / "test" / "java" / "com" / "example"
        test_dir.mkdir(parents=True)

        (test_dir / "CalcTest.java").write_text(
            "package com.example;\n"
            "import org.junit.jupiter.api.Test;\n"
            "class CalcTest {\n"
            "    @Test\n"
            "    void testAdd() { }\n"
            "    @Test\n"
            "    void testSub() { }\n"
            "    void helper() { }\n"
            "}\n"
        )

        files, cases = lang.extract_test_metadata(workspace)
        assert len(files) == 1
        assert "CalcTest.java" in files[0]
        assert len(cases) == 2
        names = {c["name"] for c in cases}
        assert names == {"testAdd", "testSub"}

    def test_extract_empty_workspace(self, lang, tmp_path):
        files, cases = lang.extract_test_metadata(tmp_path)
        assert files == []
        assert cases == []

    # -- generate_test_skeletons --

    def test_generate_skeletons(self, lang, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        interfaces = [
            {"name": "Calculator", "signature": "public class Calculator", "module": "calc"},
            {"name": "add", "signature": "public int add(int a, int b)", "module": "calc"},
        ]
        module_map = {"calc": "com.example.Calculator"}
        generated = lang.generate_test_skeletons(interfaces, module_map, workspace)
        assert len(generated) == 1
        assert "CalculatorTest.java" in generated[0]
        content = Path(workspace / generated[0]).read_text()
        assert "package com.example;" in content
        assert "class CalculatorTest" in content
        assert "@Test" in content
        assert "testAdd" in content

    # -- to_snake_case (inherited) --

    def test_snake_case(self, lang):
        assert lang.to_snake_case("Calculator") == "calculator"
        assert lang.to_snake_case("MemoryManager") == "memory_manager"


# ---------------------------------------------------------------------------
# LanguageSupport — Java Registry
# ---------------------------------------------------------------------------


class TestJavaLanguageRegistry:
    """Tests for Java language support registry."""

    def test_java_registered(self):
        lang = get_language_support("java")
        assert isinstance(lang, JavaLanguageSupport)


# ---------------------------------------------------------------------------
# TestRunner — GradleTestRunner
# ---------------------------------------------------------------------------

from toyshop.test_runner import GradleTestRunner


class TestGradleTestRunner:
    """Tests for GradleTestRunner output parsing."""

    @pytest.fixture
    def runner(self):
        return GradleTestRunner()

    def test_parse_build_successful(self, runner):
        output = "BUILD SUCCESSFUL in 5s\n3 actionable tasks: 3 executed"
        result = runner.parse_output(output)
        assert result.all_passed is True

    def test_parse_build_failed(self, runner):
        output = "BUILD FAILED in 3s\nExecution failed for task ':test'."
        result = runner.parse_output(output)
        assert result.all_passed is False

    def test_parse_test_summary(self, runner):
        output = "5 tests completed, 2 failed\nBUILD FAILED"
        result = runner.parse_output(output)
        assert result.passed == 3
        assert result.failed == 2
        assert result.all_passed is False

    def test_parse_all_passed(self, runner):
        output = "5 tests completed\nBUILD SUCCESSFUL"
        result = runner.parse_output(output)
        assert result.passed == 5
        assert result.failed == 0
        assert result.all_passed is True

    def test_parse_empty_output(self, runner):
        result = runner.parse_output("")
        assert result.all_passed is False
        assert result.total == 0

    def test_parse_junit_xml(self, runner, tmp_path):
        """Test JUnit XML report parsing."""
        report_dir = tmp_path / "build" / "test-results" / "test"
        report_dir.mkdir(parents=True)

        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.example.CalcTest" tests="3" failures="1" errors="0">
  <testcase name="testAdd" classname="com.example.CalcTest" time="0.01"/>
  <testcase name="testSub" classname="com.example.CalcTest" time="0.01">
    <failure message="expected 3 but was 5">AssertionError</failure>
  </testcase>
  <testcase name="testMul" classname="com.example.CalcTest" time="0.01"/>
</testsuite>"""
        (report_dir / "TEST-com.example.CalcTest.xml").write_text(xml_content)

        result = runner._parse_junit_xml(tmp_path)
        assert result is not None
        assert result.passed == 2
        assert result.failed == 1
        assert result.errors == 0
        assert result.total == 3
        assert result.all_passed is False
        assert len(result.per_test) == 3

    def test_parse_junit_xml_no_reports(self, runner, tmp_path):
        result = runner._parse_junit_xml(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# TestRunner — RconTestRunner
# ---------------------------------------------------------------------------

from toyshop.test_runner import RconTestRunner


class TestRconTestRunner:
    """Tests for RconTestRunner (unit-level, no live server)."""

    @pytest.fixture
    def runner(self):
        return RconTestRunner()

    def test_parse_output_all_pass(self, runner):
        output = "[PASS] block_registered:mymod:ruby\n[PASS] block_placeable:mymod:ruby\n\n2/2 passed"
        result = runner.parse_output(output)
        assert result.all_passed is True
        assert result.passed == 2
        assert result.failed == 0

    def test_parse_output_with_failure(self, runner):
        output = "[PASS] block_registered:mymod:ruby\n[FAIL] block_placeable:mymod:ruby\n\n1/2 passed"
        result = runner.parse_output(output)
        assert result.all_passed is False
        assert result.passed == 1
        assert result.failed == 1

    def test_parse_output_empty(self, runner):
        result = runner.parse_output("")
        assert result.all_passed is False
        assert result.total == 0

    def test_load_test_spec(self, tmp_path):
        import json
        spec = {"mod_id": "mymod", "blocks": ["ruby_block"], "items": ["ruby"]}
        (tmp_path / "rcon_tests.json").write_text(json.dumps(spec))
        loaded = RconTestRunner._load_test_spec(tmp_path, None)
        assert loaded is not None
        assert loaded["mod_id"] == "mymod"
        assert loaded["blocks"] == ["ruby_block"]

    def test_load_test_spec_missing(self, tmp_path):
        loaded = RconTestRunner._load_test_spec(tmp_path, None)
        assert loaded is None

    def test_run_tests_no_modfactory(self, runner, tmp_path, monkeypatch):
        """Without modfactory SDK, should return error gracefully."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("modfactory"):
                raise ImportError("no modfactory")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = runner.run_tests(tmp_path)
        assert result.errors == 1
        assert "not installed" in result.output


# ---------------------------------------------------------------------------
# TestRunner — VisualTestRunner
# ---------------------------------------------------------------------------

from toyshop.test_runner import VisualTestRunner


class TestVisualTestRunner:
    """Tests for VisualTestRunner (unit-level, no live client)."""

    @pytest.fixture
    def runner(self):
        return VisualTestRunner(vlm_api_key="test-key")

    def test_parse_output_all_pass(self, runner):
        output = "[PASS] scenario_a\n[PASS] scenario_b\n\n2/2 visual checks passed"
        result = runner.parse_output(output)
        assert result.all_passed is True
        assert result.passed == 2

    def test_parse_output_with_failure(self, runner):
        output = "[PASS] scenario_a\n[FAIL] scenario_b\n\n1/2 visual checks passed"
        result = runner.parse_output(output)
        assert result.all_passed is False
        assert result.failed == 1

    def test_load_scenarios(self, tmp_path):
        import json
        scenarios = [{"name": "test_block", "commands": ["/setblock 0 64 0 mymod:ruby"], "expectations": ["ruby block visible"]}]
        (tmp_path / "visual_scenarios.json").write_text(json.dumps(scenarios))
        loaded = VisualTestRunner._load_scenarios(tmp_path)
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["name"] == "test_block"

    def test_load_scenarios_missing(self, tmp_path):
        loaded = VisualTestRunner._load_scenarios(tmp_path)
        assert loaded is None

    def test_run_tests_no_modfactory(self, runner, tmp_path, monkeypatch):
        """Without modfactory SDK, should return error gracefully."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("modfactory"):
                raise ImportError("no modfactory")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = runner.run_tests(tmp_path)
        assert result.errors == 1
        assert "not installed" in result.output


# ---------------------------------------------------------------------------
# TestRunner — Registry completeness
# ---------------------------------------------------------------------------

from toyshop.test_runner import get_test_runner


class TestRunnerRegistry:
    """Verify all expected runners are registered."""

    def test_all_runners_registered(self):
        for framework in ("pytest", "junit", "rcon", "visual"):
            runner = get_test_runner(framework)
            assert runner is not None

    def test_unknown_runner_raises(self):
        with pytest.raises(KeyError, match="No test runner"):
            get_test_runner("nonexistent")


# ---------------------------------------------------------------------------
# Stage 5: Management level
# ---------------------------------------------------------------------------


class TestManagementLevel:
    """Tests for management_level field on ProjectType."""

    def test_default_management_level_is_standard(self):
        pt = get_project_type("python")
        assert pt.management_level == "standard"

    def test_java_management_level_is_standard(self):
        pt = get_project_type("java")
        assert pt.management_level == "standard"

    def test_json_minecraft_management_level_is_minimal(self):
        pt = get_project_type("json-minecraft")
        assert pt.management_level == "minimal"

    def test_custom_strict_management_level(self):
        strict = ProjectType(
            id="strict-test",
            language="python",
            display_name="Strict Test",
            default_artifacts=ProjectArtifacts(src=["src/"], test=["tests/"]),
            management_level="strict",
        )
        register_project_type(strict)
        fetched = get_project_type("strict-test")
        assert fetched.management_level == "strict"
