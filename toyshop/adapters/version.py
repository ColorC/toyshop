"""Version Adapter — wraps toyshop.snapshot functions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from toyshop.snapshot import (
    create_code_version,
    bidirectional_drift_check,
    CodeVersion,
)


class ASTCodeVersionAdapter:
    """Wraps existing snapshot module functions."""

    def create(
        self,
        project_dir: Path,
        project_name: str,
        *,
        ignore_patterns: list[str] | None = None,
    ) -> CodeVersion:
        return create_code_version(project_dir, project_name, ignore_patterns=ignore_patterns)

    def bidirectional_drift(self, snapshot: CodeVersion, design_md: str) -> dict[str, list[str]]:
        return bidirectional_drift_check(snapshot, design_md)
