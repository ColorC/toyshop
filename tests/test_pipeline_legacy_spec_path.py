from __future__ import annotations

from pathlib import Path

from toyshop import pipeline as legacy_pipeline


class _ReqState:
    def __init__(self):
        self.error = None
        self.current_step = "done"
        self.proposal_markdown = "# proposal\n"
        self.proposal = object()


class _ArchState:
    def __init__(self):
        self.error = None
        self.current_step = "done"
        self.design_markdown = "# design\n"
        self.tasks_markdown = "# tasks\n"
        self.spec_markdown = "# spec\n"
        self.design = None


class _DummyLLM:
    pass


def test_legacy_pipeline_writes_canonical_and_legacy_spec(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(legacy_pipeline, "create_llm", lambda: _DummyLLM())
    monkeypatch.setattr(legacy_pipeline, "run_requirement_workflow", lambda **kwargs: _ReqState())
    monkeypatch.setattr(legacy_pipeline, "run_architecture_workflow", lambda **kwargs: _ArchState())
    monkeypatch.setattr(legacy_pipeline, "init_database", lambda *args, **kwargs: None)
    monkeypatch.setattr(legacy_pipeline, "close_database", lambda *args, **kwargs: None)
    monkeypatch.setattr(legacy_pipeline, "create_project", lambda **kwargs: {"id": "p1"})

    state = legacy_pipeline.run_development_pipeline(
        user_input="build demo",
        project_name="demo",
        workspace_dir=str(tmp_path),
    )

    assert state.current_stage == "done"

    spec_canonical = tmp_path / "openspec" / "spec.md"
    spec_legacy = tmp_path / "openspec" / "specs" / "main.md"

    assert spec_canonical.exists()
    assert spec_legacy.exists()
    assert spec_canonical.read_text(encoding="utf-8") == spec_legacy.read_text(encoding="utf-8")
