"""Code Version Port — abstracts code structure scanning."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CodeVersionPort(Protocol):
    """Port for code structure scanning (formerly SnapshotPort)."""

    def create_code_version(
        self,
        project_dir: Path,
        project_name: str,
        *,
        ignore_patterns: list[str] | None = None,
    ) -> Any:
        """Scan source files and return structural version.

        Returns:
            CodeVersion-like object with modules, classes, functions.
        """
        ...

    def diff_vs_design(
        self,
        code_version: Any,
        design_md: str,
    ) -> list[str]:
        """Compare code version against design, return drift warnings.

        Returns:
            List of warning strings.
        """
        ...

    def bidirectional_drift(
        self,
        code_version: Any,
        design_md: str,
    ) -> dict[str, list[str]]:
        """Two-way drift detection.

        Returns:
            Dict with "design_only" and "code_only" keys.
        """
        ...
