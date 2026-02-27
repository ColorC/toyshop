"""Spec Adapter — wraps toyshop.openspec functions."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class OpenSpecAdapter:
    """Wraps existing openspec module functions."""

    def parse_design_md(self, design_md: str) -> tuple[list[dict], list[dict]]:
        from toyshop.snapshot import _parse_design_modules, _parse_design_interfaces
        return _parse_design_modules(design_md), _parse_design_interfaces(design_md)

    def validate_openspec_dir(self, doc_dir: Path, strict: bool = False) -> Any:
        from toyshop.openspec import validator
        return validator.validate_openspec_dir(doc_dir, strict=strict)
