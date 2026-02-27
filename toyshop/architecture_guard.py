"""Architecture guard — enforce structural health invariants.

Three categories of checks:
1. Duplicate responsibility detection (cross-module)
2. New-module overlap detection (brownfield)
3. Interface quality checks (naming, params, types)

All fast checks are pure Python (no LLM). An optional LLM-based semantic
overlap check is available for strict management level.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field, asdict
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from toyshop.llm import LLM


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class GuardViolation:
    """A single architecture guard violation."""

    check_name: str    # "duplicate_responsibility" | "new_module_overlap" | "interface_quality"
    severity: str      # "error" | "warning"
    module_a: str
    module_b: str      # empty for single-module checks
    detail: str
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GuardResult:
    """Result of running all architecture guards."""

    violations: list[GuardViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(v.severity == "error" for v in self.violations)

    @property
    def errors(self) -> list[GuardViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[GuardViolation]:
        return [v for v in self.violations if v.severity == "warning"]


# ---------------------------------------------------------------------------
# Tokenization helper
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase split into word tokens (supports CJK characters)."""
    return {t for t in _SPLIT_RE.split(text.lower()) if len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Check 1: Duplicate responsibilities across modules
# ---------------------------------------------------------------------------


def check_duplicate_responsibilities(
    modules: list[dict[str, Any]],
    warn_threshold: float = 0.6,
    error_threshold: float = 0.8,
) -> list[GuardViolation]:
    """Detect modules with overlapping responsibilities using token-set Jaccard."""
    violations: list[GuardViolation] = []

    # Build token sets per module
    mod_tokens: list[tuple[str, set[str]]] = []
    for m in modules:
        resps = m.get("responsibilities") or []
        if not resps:
            continue
        tokens = set()
        for r in resps:
            tokens |= _tokenize(r)
        if tokens:
            mod_tokens.append((m.get("name", "?"), tokens))

    # Pairwise comparison
    for i in range(len(mod_tokens)):
        for j in range(i + 1, len(mod_tokens)):
            name_a, tok_a = mod_tokens[i]
            name_b, tok_b = mod_tokens[j]
            sim = _jaccard(tok_a, tok_b)
            if sim >= error_threshold:
                violations.append(GuardViolation(
                    check_name="duplicate_responsibility",
                    severity="error",
                    module_a=name_a, module_b=name_b,
                    detail=f"模块 {name_a} 与 {name_b} 职责高度重复 (相似度 {sim:.0%})",
                    suggestion=f"合并 {name_a} 和 {name_b}，或重新划分职责边界",
                ))
            elif sim >= warn_threshold:
                violations.append(GuardViolation(
                    check_name="duplicate_responsibility",
                    severity="warning",
                    module_a=name_a, module_b=name_b,
                    detail=f"模块 {name_a} 与 {name_b} 职责部分重叠 (相似度 {sim:.0%})",
                    suggestion=f"检查 {name_a} 和 {name_b} 是否存在不必要的职责交叉",
                ))

    return violations


# ---------------------------------------------------------------------------
# Check 2: New module overlap with existing modules
# ---------------------------------------------------------------------------


def check_new_module_overlap(
    new_modules: list[dict[str, Any]],
    existing_modules: list[dict[str, Any]],
    warn_threshold: float = 0.5,
) -> list[GuardViolation]:
    """Check if new modules duplicate functionality of existing modules."""
    violations: list[GuardViolation] = []

    existing_tokens: list[tuple[str, set[str]]] = []
    for m in existing_modules:
        resps = m.get("responsibilities") or []
        tokens = set()
        for r in resps:
            tokens |= _tokenize(r)
        if tokens:
            existing_tokens.append((m.get("name", "?"), tokens))

    for nm in new_modules:
        new_resps = nm.get("responsibilities") or []
        new_tok = set()
        for r in new_resps:
            new_tok |= _tokenize(r)
        if not new_tok:
            continue

        for ex_name, ex_tok in existing_tokens:
            sim = _jaccard(new_tok, ex_tok)
            if sim >= warn_threshold:
                violations.append(GuardViolation(
                    check_name="new_module_overlap",
                    severity="warning",
                    module_a=nm.get("name", "?"), module_b=ex_name,
                    detail=f"新模块 {nm.get('name', '?')} 与已有模块 {ex_name} 功能重叠 (相似度 {sim:.0%})",
                    suggestion=f"考虑扩展 {ex_name} 而非新建模块",
                ))

    return violations


# ---------------------------------------------------------------------------
# Check 3: Interface quality
# ---------------------------------------------------------------------------

_SNAKE_RE = re.compile(r"^_?[a-z][a-z0-9_]*$")
_PASCAL_RE = re.compile(r"^_?[A-Z][a-zA-Z0-9]*$")


def _count_params(signature: str) -> int | None:
    """Count parameters from a function signature string. Returns None if unparseable."""
    sig = signature.strip()
    if not sig.startswith("def "):
        return None
    # Ensure it ends with a colon for ast.parse
    if not sig.endswith(":"):
        sig += ":\n    pass"
    else:
        sig += "\n    pass"
    try:
        tree = ast.parse(sig)
        func = tree.body[0]
        if not isinstance(func, ast.FunctionDef):
            return None
        args = func.args
        total = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
        # Exclude 'self' and 'cls'
        if args.args and args.args[0].arg in ("self", "cls"):
            total -= 1
        return total
    except (SyntaxError, IndexError):
        return None


def check_interface_quality(
    interfaces: list[dict[str, Any]],
    max_params: int = 7,
) -> list[GuardViolation]:
    """Check interface quality: param count, naming, return types."""
    violations: list[GuardViolation] = []
    seen: dict[str, list[str]] = {}  # (module_id, name) -> list of modules

    for intf in interfaces:
        name = intf.get("name", "")
        typ = intf.get("type", "function")
        sig = intf.get("signature", "")
        module_id = intf.get("module_id", "")

        # Duplicate name within same module
        key = f"{module_id}:{name}"
        seen.setdefault(key, []).append(name)
        if len(seen[key]) == 2:  # Report once
            violations.append(GuardViolation(
                check_name="interface_quality",
                severity="warning",
                module_a=module_id, module_b="",
                detail=f"接口 {name} 在同一模块中重复定义",
                suggestion="移除重复定义或重命名",
            ))

        # Naming convention
        if typ == "function" and name and not name.startswith("_"):
            if not _SNAKE_RE.match(name):
                violations.append(GuardViolation(
                    check_name="interface_quality",
                    severity="warning",
                    module_a=module_id, module_b="",
                    detail=f"函数 {name} 不符合 snake_case 命名规范",
                    suggestion=f"重命名为 snake_case 格式",
                ))
        elif typ == "class" and name:
            if not _PASCAL_RE.match(name):
                violations.append(GuardViolation(
                    check_name="interface_quality",
                    severity="warning",
                    module_a=module_id, module_b="",
                    detail=f"类 {name} 不符合 PascalCase 命名规范",
                    suggestion=f"重命名为 PascalCase 格式",
                ))

        # Parameter count (functions only)
        if typ == "function" and sig:
            count = _count_params(sig)
            if count is not None and count > max_params:
                violations.append(GuardViolation(
                    check_name="interface_quality",
                    severity="warning",
                    module_a=module_id, module_b="",
                    detail=f"函数 {name} 参数过多 ({count} > {max_params})",
                    suggestion="考虑使用配置对象或拆分函数",
                ))

        # Missing return type (functions only)
        if typ == "function" and sig and sig.startswith("def ") and "->" not in sig:
            violations.append(GuardViolation(
                check_name="interface_quality",
                severity="warning",
                module_a=module_id, module_b="",
                detail=f"函数 {name} 缺少返回类型注解",
                suggestion="添加 -> 返回类型注解",
            ))

    return violations


# ---------------------------------------------------------------------------
# Optional: LLM semantic overlap check
# ---------------------------------------------------------------------------


def check_semantic_overlap(
    modules: list[dict[str, Any]],
    llm: "LLM",
) -> list[GuardViolation]:
    """LLM-based semantic similarity check for module responsibilities.

    Single LLM call with all module descriptions. Only used in strict mode.
    """
    from toyshop.llm import chat_with_tool

    # Build module summary
    lines = []
    for m in modules:
        resps = m.get("responsibilities") or []
        if resps:
            lines.append(f"- {m.get('name', '?')}: {'; '.join(resps)}")
    if len(lines) < 2:
        return []

    prompt = (
        "以下是一个项目的模块及其职责列表。请识别职责语义重叠的模块对。\n"
        "只报告真正有重叠的对，不要报告互补关系。\n\n"
        + "\n".join(lines)
    )

    tool_schema = {
        "type": "object",
        "properties": {
            "overlaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "module_a": {"type": "string"},
                        "module_b": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["module_a", "module_b", "reason"],
                },
            },
        },
        "required": ["overlaps"],
    }

    try:
        result = chat_with_tool(
            llm, prompt, "report_overlaps", tool_schema,
            system="你是架构审查专家。只报告真正的职责重叠，不要误报。",
        )
    except Exception:
        return []

    violations: list[GuardViolation] = []
    for overlap in result.get("overlaps", []):
        violations.append(GuardViolation(
            check_name="semantic_overlap",
            severity="warning",
            module_a=overlap.get("module_a", "?"),
            module_b=overlap.get("module_b", "?"),
            detail=f"语义重叠: {overlap.get('reason', '')}",
            suggestion="合并或重新划分职责",
        ))

    return violations


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def run_architecture_guard(
    modules: list[dict[str, Any]],
    interfaces: list[dict[str, Any]] | None = None,
    new_modules: list[dict[str, Any]] | None = None,
    management_level: str = "standard",
    llm: "LLM | None" = None,
) -> GuardResult:
    """Run all architecture guards and return unified result.

    management_level controls behavior:
      "minimal"  — skip all guards
      "standard" — run fast checks, violations are warnings
      "strict"   — run fast + LLM checks, Jaccard errors stay as errors
    """
    if management_level == "minimal":
        return GuardResult()

    result = GuardResult()

    # 1. Duplicate responsibilities
    result.violations.extend(check_duplicate_responsibilities(modules))

    # 2. New module overlap
    if new_modules:
        result.violations.extend(check_new_module_overlap(new_modules, modules))

    # 3. Interface quality
    if interfaces:
        result.violations.extend(check_interface_quality(interfaces))

    # 4. LLM semantic check (strict only)
    if management_level == "strict" and llm is not None:
        result.violations.extend(check_semantic_overlap(modules, llm))

    # In standard mode, downgrade all errors to warnings
    if management_level == "standard":
        for v in result.violations:
            v.severity = "warning"

    return result
