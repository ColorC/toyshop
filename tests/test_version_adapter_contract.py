from __future__ import annotations

from pathlib import Path

from toyshop.adapters.version import ASTCodeVersionAdapter


def test_ast_version_adapter_contract_methods(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    adapter = ASTCodeVersionAdapter()
    snapshot = adapter.create_code_version(proj, "demo")

    design_md = """
## Modules
- **core**
  - **Path:** `a.py`

## Interfaces
- `foo()`
"""

    warnings = adapter.diff_vs_design(snapshot, design_md)
    drift = adapter.bidirectional_drift(snapshot, design_md)

    assert isinstance(warnings, list)
    assert isinstance(drift, dict)
    assert "design_only" in drift and "code_only" in drift


def test_ast_version_adapter_legacy_create_alias(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    adapter = ASTCodeVersionAdapter()
    s1 = adapter.create_code_version(proj, "demo")
    s2 = adapter.create(proj, "demo")

    assert len(s1.modules) == len(s2.modules)
