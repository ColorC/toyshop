"""Reporting adapters for ToyShop framework."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from toyshop.ports.reporting import ReportingPort


class FileReportingAdapter:
    """Publish reporting events into run artifacts on disk."""

    def __init__(self, default_filename: str = "mid_report_hook.json"):
        self._default_filename = default_filename

    def publish(self, event: dict[str, Any], *, run_dir: Path | None = None) -> None:
        if run_dir is None:
            raise ValueError("run_dir is required for FileReportingAdapter")

        output = run_dir / self._default_filename
        output.write_text(_to_json(event), encoding="utf-8")


def _to_json(data: dict[str, Any]) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, indent=2)
