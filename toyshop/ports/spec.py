"""Spec Port — abstracts OpenSpec document generation and parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SpecPort(Protocol):
    """Port for OpenSpec document generation and parsing."""

    def generate_proposal(
        self,
        user_input: str,
        project_name: str,
    ) -> dict[str, Any]:
        """Generate proposal.md content.

        Returns:
            Dict with content key (markdown string).
        """
        ...

    def generate_design(
        self,
        proposal: dict[str, Any],
        requirement: str,
    ) -> dict[str, Any]:
        """Generate design.md content from proposal.

        Returns:
            Dict with content key (markdown string).
        """
        ...

    def generate_tasks(
        self,
        design: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate tasks.md from design.

        Returns:
            Dict with content key (markdown string).
        """
        ...

    def generate_spec(
        self,
        tasks: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate spec.md from tasks.

        Returns:
            Dict with content key (markdown string).
        """
        ...

    def parse_design_md(
        self,
        design_md: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Parse design.md into (modules, interfaces).

        Returns:
            Tuple of (modules list, interfaces list).
        """
        ...

    def validate(
        self,
        doc_dir: Path,
        strict: bool = False,
    ) -> Any:
        """Validate openspec documents in directory.

        Returns:
            ValidationReport-like object.
        """
        ...
