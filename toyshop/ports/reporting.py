"""Reporting Port — abstracts progress/mid-report publishing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ReportingPort(Protocol):
    """Port for publishing progress and requesting intervention."""

    def publish(self, event: dict[str, Any], *, run_dir: Path | None = None) -> None:
        """Publish a structured progress event.

        Args:
            event: Event payload
            run_dir: Optional run directory for artifact-backed adapters
        """
        ...
