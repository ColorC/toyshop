"""LanguageSupport abstract base class and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class LanguageSupport(ABC):
    """Abstract interface for language-specific operations in the TDD pipeline."""

    @abstractmethod
    def normalize_signature(self, name: str, sig: str) -> str:
        """Normalize a raw signature string into valid language syntax."""
        ...

    @abstractmethod
    def is_valid_signature(self, sig: str) -> bool:
        """Check if a signature looks like valid code for this language."""
        ...

    @abstractmethod
    def generate_stub_for_module(self, ifaces: list[dict[str, str]]) -> str:
        """Generate stub source code for a single module's interfaces."""
        ...

    @abstractmethod
    def generate_test_skeletons(
        self,
        interfaces: list[dict[str, str]],
        module_map: dict[str, str],
        workspace: Path,
        mode: str = "create",
    ) -> list[str]:
        """Generate test skeleton files. Returns list of generated file paths (relative to workspace)."""
        ...

    @abstractmethod
    def build_smoke_command(self, stub_modules: list[str]) -> str:
        """Build a shell command to smoke-test that stubs are importable."""
        ...

    @abstractmethod
    def extract_test_metadata(
        self, workspace: Path,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Scan test directory and extract test case metadata.

        Returns (test_files, test_cases) where test_cases have keys:
        id, name, file, class_name.
        """
        ...

    @abstractmethod
    def module_path_from_file(self, file_path: str) -> str:
        """Convert a file path like 'calculator/core.py' to an import path like 'calculator.core'."""
        ...

    @abstractmethod
    def build_module_map(self, modules: list[dict[str, str]]) -> dict[str, str]:
        """Build short_id → import_path mapping from parsed design modules."""
        ...

    def to_snake_case(self, name: str) -> str:
        """Convert CamelCase to snake_case. Shared utility."""
        import re
        s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
        return s.lower()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_LANG_REGISTRY: dict[str, LanguageSupport] = {}


def register_language_support(language: str, support: LanguageSupport) -> None:
    """Register a LanguageSupport instance for a language ID."""
    _LANG_REGISTRY[language] = support


def get_language_support(language: str) -> LanguageSupport:
    """Get LanguageSupport by language ID. Raises KeyError if not found."""
    if language not in _LANG_REGISTRY:
        available = ", ".join(sorted(_LANG_REGISTRY.keys()))
        raise KeyError(f"No language support for '{language}'. Available: {available}")
    return _LANG_REGISTRY[language]
