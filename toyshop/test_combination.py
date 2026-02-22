"""Black-box / White-box test combination with anti-cheat mechanism.

When a black-box test fails:
1. Expose it as a white-box test (copy to whitebox test file)
2. Generate a variant test with different inputs on the same API route
3. Coding Agent must pass both — prevents hardcoding expected outputs
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openhands.sdk import LLM


def expose_as_whitebox(
    failing_test_names: list[str],
    bb_test_file: Path,
    wb_target: Path,
) -> list[str]:
    """Copy failing black-box tests to a white-box test file.

    Args:
        failing_test_names: Names of failing test functions (e.g. ["test_tc_007"])
        bb_test_file: Path to tests/test_blackbox_auto.py
        wb_target: Path to tests/test_whitebox_from_bb.py

    Returns:
        List of test names that were exposed.
    """
    if not bb_test_file.exists():
        return []

    source = bb_test_file.read_text(encoding="utf-8")
    exposed = []

    # Parse the source to extract function definitions
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Collect import lines (top of file)
    lines = source.split("\n")
    import_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            import_lines.append(line)
        elif stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''"):
            break

    # Extract function source for each failing test
    func_sources = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in failing_test_names:
                # Get source lines for this function
                start = node.lineno - 1
                end = node.end_lineno or start + 1
                func_source = "\n".join(lines[start:end])
                # Rename to avoid collision: test_tc_007 → test_wb_tc_007
                renamed = func_source.replace(
                    f"def {node.name}",
                    f"def {node.name.replace('test_', 'test_wb_')}",
                    1,
                )
                func_sources.append(renamed)
                exposed.append(node.name)

        # Also handle class-based tests
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in failing_test_names:
                        start = node.lineno - 1
                        end = node.end_lineno or start + 1
                        func_source = "\n".join(lines[start:end])
                        func_sources.append(func_source)
                        exposed.append(item.name)

    if not func_sources:
        return []

    # Build or append to whitebox file
    if wb_target.exists():
        existing = wb_target.read_text(encoding="utf-8")
        new_content = existing.rstrip() + "\n\n" + "\n\n".join(func_sources) + "\n"
    else:
        header = '"""White-box tests exposed from failing black-box tests."""\n\n'
        imports = "\n".join(import_lines) + "\n\n" if import_lines else ""
        new_content = header + imports + "\n\n".join(func_sources) + "\n"

    wb_target.write_text(new_content, encoding="utf-8")
    return exposed


VARIANT_PROMPT = """You are generating a variant test to prevent answer-cheating.

Given this original test:
```python
{original_test}
```

And this spec scenario:
{scenario_description}

Generate ONE variant test function that:
1. Calls the EXACT same API/function as the original test
2. Uses COMPLETELY DIFFERENT input values (no overlap with original)
3. Has the CORRECT expected result for the new inputs
4. Has the same import structure as the original
5. Function name: {variant_name}

Return ONLY the Python function code, no explanation.
"""


def generate_variant_test(
    scenario: dict,
    original_test_code: str,
    variant_name: str,
    llm: "LLM",
) -> str:
    """Use LLM to generate a variant test with different inputs.

    Args:
        scenario: Parsed spec.md scenario dict with id, name, given, when, then
        original_test_code: Source code of the original test
        variant_name: Name for the variant function (e.g. test_tc_007_variant)
        llm: LLM instance

    Returns:
        Python source code for the variant test function.
    """
    scenario_desc = (
        f"ID: {scenario.get('id', '?')}\n"
        f"Name: {scenario.get('name', '?')}\n"
        f"Given: {scenario.get('given', '?')}\n"
        f"When: {scenario.get('when', '?')}\n"
        f"Then: {scenario.get('then', '?')}"
    )

    prompt = VARIANT_PROMPT.format(
        original_test=original_test_code,
        scenario_description=scenario_desc,
        variant_name=variant_name,
    )

    response = llm.completion(
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract code from response
    content = response.choices[0].message.content or ""

    # Try to extract from code block
    code_match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
    if code_match:
        return code_match.group(1).strip()

    # Try to find a function definition
    func_match = re.search(r"(def \w+\(.*?\n(?:[ \t]+.+\n)*)", content)
    if func_match:
        return func_match.group(1).strip()

    return content.strip()


def generate_variant_tests_for_failures(
    failing_test_names: list[str],
    bb_test_file: Path,
    scenarios: list[dict],
    llm: "LLM",
    variant_file: Path,
) -> list[str]:
    """Generate variant tests for all failing black-box tests.

    Returns list of variant test names created.
    """
    if not bb_test_file.exists():
        return []

    source = bb_test_file.read_text(encoding="utf-8")
    lines = source.split("\n")

    # Extract import lines
    import_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            import_lines.append(line)
        elif stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
            break

    # Parse to find function bodies
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Map test name → source code
    test_sources: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in failing_test_names:
                start = node.lineno - 1
                end = node.end_lineno or start + 1
                test_sources[node.name] = "\n".join(lines[start:end])

    # Map test name → scenario
    test_to_scenario: dict[str, dict] = {}
    for name in failing_test_names:
        # test_tc_007 → TC-007
        tc_id = name.replace("test_", "").replace("_", "-").upper()
        for s in scenarios:
            if s.get("id", "").upper() == tc_id:
                test_to_scenario[name] = s
                break

    variants = []
    variant_codes = []

    for name in failing_test_names:
        if name not in test_sources:
            continue
        scenario = test_to_scenario.get(name, {"id": name, "name": name, "given": "", "when": "", "then": ""})
        variant_name = f"{name}_variant"

        code = generate_variant_test(
            scenario=scenario,
            original_test_code=test_sources[name],
            variant_name=variant_name,
            llm=llm,
        )
        if code:
            variant_codes.append(code)
            variants.append(variant_name)

    if not variant_codes:
        return []

    # Write variant file
    header = '"""Anti-cheat variant tests — same API routes, different inputs."""\n\n'
    imports = "\n".join(import_lines) + "\n\n" if import_lines else ""
    content = header + imports + "\n\n".join(variant_codes) + "\n"
    variant_file.write_text(content, encoding="utf-8")

    return variants


# =============================================================================
# Anti-cheat for flipped tests (v2 Debug Form flow)
# =============================================================================

FLIPPED_VARIANT_PROMPT = """You are generating an anti-cheat variant test.

A test that was previously FAILING is now PASSING after code changes.
Generate a variant test with DIFFERENT inputs to verify the fix is genuine (not hardcoded).

Original test:
```python
{original_test}
```

Generate ONE variant test function that:
1. Calls the EXACT same API/function as the original test
2. Uses COMPLETELY DIFFERENT input values
3. Has the CORRECT expected result for the new inputs
4. Function name: {variant_name}

Return ONLY the Python function code, no explanation.
"""


def generate_anticheat_tests_for_flipped(
    flipped_test_ids: list[str],
    test_dir: Path,
    llm: "LLM",
    output_file: Path,
) -> list[str]:
    """Generate anti-cheat variant tests for tests that flipped from failing to passing.

    Args:
        flipped_test_ids: Test IDs like "tests/test_calc.py::test_add"
        test_dir: Path to tests/ directory
        llm: LLM instance
        output_file: Where to write variant tests

    Returns:
        List of variant test names created.
    """
    # Group by file
    file_tests: dict[str, list[str]] = {}
    for tid in flipped_test_ids:
        if "::" in tid:
            fpath, tname = tid.split("::", 1)
            file_tests.setdefault(fpath, []).append(tname)

    all_import_lines: list[str] = []
    variant_codes: list[str] = []
    variant_names: list[str] = []

    for fpath, test_names in file_tests.items():
        full_path = test_dir.parent / fpath  # test_dir is workspace/tests, fpath is tests/xxx.py
        if not full_path.exists():
            continue

        source = full_path.read_text(encoding="utf-8")
        lines = source.split("\n")

        # Collect imports
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                if line not in all_import_lines:
                    all_import_lines.append(line)
            elif stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
                break

        # Parse to find function bodies
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in test_names:
                    start = node.lineno - 1
                    end = node.end_lineno or start + 1
                    test_source = "\n".join(lines[start:end])
                    variant_name = f"{node.name}_anticheat"

                    prompt = FLIPPED_VARIANT_PROMPT.format(
                        original_test=test_source,
                        variant_name=variant_name,
                    )
                    try:
                        response = llm.completion(
                            messages=[{"role": "user", "content": prompt}],
                        )
                        content = response.choices[0].message.content or ""
                        # Extract code
                        code_match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
                        if code_match:
                            code = code_match.group(1).strip()
                        else:
                            func_match = re.search(r"(def \w+\(.*?\n(?:[ \t]+.+\n)*)", content)
                            code = func_match.group(1).strip() if func_match else content.strip()

                        if code:
                            variant_codes.append(code)
                            variant_names.append(variant_name)
                    except Exception:
                        pass  # skip on LLM error

    if not variant_codes:
        return []

    header = '"""Anti-cheat variant tests for flipped tests."""\n\n'
    imports = "\n".join(all_import_lines) + "\n\n" if all_import_lines else ""
    file_content = header + imports + "\n\n".join(variant_codes) + "\n"
    output_file.write_text(file_content, encoding="utf-8")

    return variant_names
