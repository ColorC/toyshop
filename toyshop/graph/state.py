"""Shared graph state types for ToyShop orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DevGraphState:
    """Serializable state for dev pipeline graph.

    This mirrors pm.run_batch stages but is intentionally minimal in Phase 5.
    """

    pm_root: str | Path
    project_name: str
    user_input: str
    project_type: str = "python"

    batch_id: str | None = None
    batch_dir: str | None = None
    status: str = "pending"
    error: str | None = None

    # Artifacts (optional snapshots)
    proposal_md: str | None = None
    design_md: str | None = None
    tasks_md: str | None = None
    spec_md: str | None = None

    meta: dict[str, Any] | None = None
