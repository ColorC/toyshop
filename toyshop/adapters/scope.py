"""Scope Control Adapter — implements scope validation for protected files."""

from __future__ import annotations

from typing import Any

from toyshop.ports.scope import ScopeControlPort
from toyshop.self_host import PROTECTED_FILES


class ScopeControlAdapter:
    """Implements scope validation using PROTECTED_FILES."""

    def validate_write(
        self,
        path: str,
        allowed_paths: list[str],
        forbidden_paths: list[str],
    ) -> bool:
        # First check if path is in forbidden (protected) files
        if path in PROTECTED_FILES:
            return False
        # Then check if path matches allowed patterns
        for pattern in allowed_paths:
            if self._matches_pattern(path, pattern):
                return True
        return False

    def check_protected(self, path: str) -> bool:
        return path in PROTECTED_FILES

    @staticmethod
    def _matches_pattern(path: str, pattern: str) -> bool:
        """Check if path matches a glob-like pattern."""
        import fnmatch
        parts = path.split("/")
        pattern_parts = pattern.split("/")
        for i in range(len(parts)):
            subpath = "/".join(parts[:i+1])
            if fnmatch.fnmatch(subpath, pattern):
                return True
        return False
