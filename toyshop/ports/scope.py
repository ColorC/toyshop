"""Scope Control Port — abstracts modification scope validation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScopeControlPort(Protocol):
    """Port for modification scope validation."""

    def validate_write(
        self,
        path: str,
        allowed_paths: list[str],
        forbidden_paths: list[str],
    ) -> bool:
        """Check if a write to path is allowed.

        Returns:
            True if allowed, False if forbidden.
        """
        ...

    def check_protected(self, path: str) -> bool:
        """Check if path is a protected file.

        Returns:
            True if protected (cannot be modified).
        """
        ...

    def create_change_plan(
        self,
        allowed_paths: list[str],
        forbidden_paths: list[str],
        max_files: int = 20,
        max_lines_added: int = 1000,
    ) -> str:
        """Create a change plan with scope constraints.

        Returns:
            Change plan ID.
        """
        ...
