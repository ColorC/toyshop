"""Tests for test skeleton generation from design.md contracts."""

import pytest
import tempfile
import shutil
from pathlib import Path

from toyshop.tdd_pipeline import (
    _build_module_map,
    _parse_design_modules,
    _parse_design_interfaces,
    _generate_test_skeletons,
    extract_signatures,
)


# Minimal design.md in Format B (LLM-generated style)
SAMPLE_DESIGN_MD = """\
# Technical Design

## Architecture

### Modules
#### Exceptions (`exceptions`)
- **File:** `calculator/exceptions.py`

#### Calculator Core (`core`)
- **File:** `calculator/core.py`

#### Memory Manager (`memory`)
- **File:** `calculator/memory.py`

#### Public API (`api`)
- **File:** `calculator/__init__.py`

### Interfaces
#### CalculatorError (`exc-base`)
- **Type:** class
- **Module:** `exceptions`
- **Signature:** `class CalculatorError(Exception):`

#### DivisionByZeroError (`exc-divzero`)
- **Type:** class
- **Module:** `exceptions`
- **Signature:** `class DivisionByZeroError(CalculatorError):`
"""

SAMPLE_DESIGN_MD_PART2 = """\

#### Calculator (`core-calc`)
- **Type:** class
- **Module:** `core`
- **Signature:** `class Calculator:`

#### Calculator.add (`core-add`)
- **Type:** method
- **Module:** `core`
- **Signature:** `def add(self, value: float) -> Calculator:`

#### Calculator.subtract (`core-sub`)
- **Type:** method
- **Module:** `core`
- **Signature:** `def subtract(self, value: float) -> Calculator:`

#### Calculator.divide (`core-div`)
- **Type:** method
- **Module:** `core`
- **Signature:** `def divide(self, value: float) -> Calculator:`

#### Calculator.reset (`core-reset`)
- **Type:** method
- **Module:** `core`
- **Signature:** `def reset(self) -> Calculator:`

#### Calculator.result (`core-result`)
- **Type:** method
- **Module:** `core`
- **Signature:** `def result(self) -> float:`

#### MemoryManager (`mem-manager`)
- **Type:** class
- **Module:** `memory`
- **Signature:** `class MemoryManager:`

#### MemoryManager.store (`mem-store`)
- **Type:** method
- **Module:** `memory`
- **Signature:** `def store(self, key: str, value: float) -> None:`

#### MemoryManager.retrieve (`mem-retrieve`)
- **Type:** method
- **Module:** `memory`
- **Signature:** `def retrieve(self, key: str) -> float:`

#### create_calculator (`api-create`)
- **Type:** function
- **Module:** `api`
- **Signature:** `def create_calculator(initial_value: float = 0) -> Calculator:`
"""

FULL_DESIGN_MD = SAMPLE_DESIGN_MD + SAMPLE_DESIGN_MD_PART2


class TestBuildModuleMap:
    """Tests for _build_module_map()."""

    def test_format_b_modules(self):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        assert module_map["exceptions"] == "calculator.exceptions"
        assert module_map["core"] == "calculator.core"
        assert module_map["memory"] == "calculator.memory"
        # __init__.py → package import
        assert module_map["api"] == "calculator"

    def test_empty_modules(self):
        assert _build_module_map([]) == {}

    def test_module_without_filepath(self):
        modules = [{"name": "Foo (`foo`)", "filePath": ""}]
        assert _build_module_map(modules) == {}

    def test_format_a_modules(self):
        modules = [{"name": "mdtable.parser", "filePath": "mdtable/parser.py"}]
        module_map = _build_module_map(modules)
        assert module_map["parser"] == "mdtable.parser"


class TestGenerateTestSkeletons:
    """Tests for _generate_test_skeletons()."""

    @pytest.fixture
    def workspace(self):
        ws = tempfile.mkdtemp(prefix="skeleton_test_")
        yield Path(ws)
        shutil.rmtree(ws, ignore_errors=True)

    def test_generates_files_per_module(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        skeleton_files = _generate_test_skeletons(interfaces, module_map, workspace)

        # Should generate test files for exceptions, core, memory, api
        assert len(skeleton_files) >= 3
        filenames = [Path(f).name for f in skeleton_files]
        assert "test_core.py" in filenames
        assert "test_memory.py" in filenames
        assert "test_exceptions.py" in filenames

    def test_correct_imports_in_core(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        _generate_test_skeletons(interfaces, module_map, workspace)

        core_test = (workspace / "tests" / "test_core.py").read_text()
        # Must import from calculator.core, NOT calculator.exceptions
        assert "from calculator.core import" in core_test
        assert "Calculator" in core_test

    def test_correct_imports_in_memory(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        _generate_test_skeletons(interfaces, module_map, workspace)

        mem_test = (workspace / "tests" / "test_memory.py").read_text()
        assert "from calculator.memory import" in mem_test
        assert "MemoryManager" in mem_test

    def test_cross_module_exception_imports(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        _generate_test_skeletons(interfaces, module_map, workspace)

        core_test = (workspace / "tests" / "test_core.py").read_text()
        # Core tests should also import exceptions for error testing
        assert "from calculator.exceptions import" in core_test

    def test_fixtures_generated(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        _generate_test_skeletons(interfaces, module_map, workspace)

        core_test = (workspace / "tests" / "test_core.py").read_text()
        assert "@pytest.fixture" in core_test
        assert "def calculator():" in core_test
        assert "return Calculator()" in core_test

    def test_test_methods_generated(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        _generate_test_skeletons(interfaces, module_map, workspace)

        core_test = (workspace / "tests" / "test_core.py").read_text()
        assert "class TestCalculator:" in core_test
        assert "def test_add(" in core_test
        assert "def test_subtract(" in core_test
        assert "def test_divide(" in core_test

    def test_api_function_tests(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        _generate_test_skeletons(interfaces, module_map, workspace)

        api_test = (workspace / "tests" / "test_api.py").read_text()
        assert "from calculator import" in api_test
        assert "def test_create_calculator():" in api_test

    def test_modify_mode_preserves_existing(self, workspace):
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        # First generate
        _generate_test_skeletons(interfaces, module_map, workspace, mode="create")
        original = (workspace / "tests" / "test_core.py").read_text()

        # Modify the file
        (workspace / "tests" / "test_core.py").write_text("# custom content\n")

        # Re-generate in modify mode — should NOT overwrite
        _generate_test_skeletons(interfaces, module_map, workspace, mode="modify")
        assert (workspace / "tests" / "test_core.py").read_text() == "# custom content\n"

    def test_no_init_module_in_import(self, workspace):
        """__init__.py modules should import as package, not package.__init__."""
        modules = _parse_design_modules(FULL_DESIGN_MD)
        interfaces = _parse_design_interfaces(FULL_DESIGN_MD)
        module_map = _build_module_map(modules)

        _generate_test_skeletons(interfaces, module_map, workspace)

        api_test = (workspace / "tests" / "test_api.py").read_text()
        assert "__init__" not in api_test


class TestExtractSignaturesWithSkeletons:
    """Integration test: extract_signatures now generates skeletons."""

    @pytest.fixture
    def workspace(self):
        ws = tempfile.mkdtemp(prefix="skeleton_integ_")
        yield Path(ws)
        shutil.rmtree(ws, ignore_errors=True)

    def test_manifest_includes_skeleton_files(self, workspace):
        openspec = workspace / "openspec"
        openspec.mkdir(parents=True)
        (openspec / "design.md").write_text(FULL_DESIGN_MD)

        manifest = extract_signatures(workspace)

        assert len(manifest.skeleton_files) >= 3
        assert any("test_core.py" in f for f in manifest.skeleton_files)
        assert any("test_memory.py" in f for f in manifest.skeleton_files)
