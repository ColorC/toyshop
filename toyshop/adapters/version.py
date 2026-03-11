"""Version Adapter — wraps toyshop.snapshot functions."""

from __future__ import annotations

from pathlib import Path

from toyshop.snapshot import (
    create_code_version,
    diff_version_vs_design,
    bidirectional_drift_check,
    CodeVersion,
)


class ASTCodeVersionAdapter:
    """Wraps existing snapshot module functions."""

    def create_code_version(
        self,
        project_dir: Path,
        project_name: str,
        *,
        ignore_patterns: list[str] | None = None,
    ) -> CodeVersion:
        return create_code_version(project_dir, project_name, ignore_patterns=ignore_patterns)

    def create(
        self,
        project_dir: Path,
        project_name: str,
        *,
        ignore_patterns: list[str] | None = None,
    ) -> CodeVersion:
        """Backward-compatible alias for older call sites."""
        return self.create_code_version(
            project_dir,
            project_name,
            ignore_patterns=ignore_patterns,
        )

    def diff_vs_design(self, code_version: CodeVersion, design_md: str) -> list[str]:
        return diff_version_vs_design(code_version, design_md)

    def bidirectional_drift(self, code_version: CodeVersion, design_md: str) -> dict[str, list[str]]:
        return bidirectional_drift_check(code_version, design_md)
