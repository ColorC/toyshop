from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from toyshop.graph.dev_pipeline import run_dev_graph
from toyshop.graph.state import DevGraphState
from toyshop.pm import create_batch, run_batch


def test_run_batch_graph_path_uses_graph_once(monkeypatch, tmp_path: Path):
    seeded = create_batch(tmp_path, "demo", "build demo")
    called = {"count": 0}

    def fake_run_dev_graph(state, *, llm):
        called["count"] += 1
        state.batch_dir = str(seeded.batch_dir)
        state.batch_id = seeded.batch_id
        state.status = seeded.status
        state.error = seeded.error
        return state

    monkeypatch.setenv("TOYSHOP_USE_GRAPH", "1")
    monkeypatch.setattr("toyshop.graph.dev_pipeline.run_dev_graph", fake_run_dev_graph)

    batch = run_batch(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build demo",
        llm=object(),
    )

    assert called["count"] == 1
    assert batch.batch_dir == seeded.batch_dir
    assert batch.batch_id == seeded.batch_id


def test_run_dev_graph_calls_run_batch_with_allow_graph_false(monkeypatch, tmp_path: Path):
    openspec = tmp_path / "batch" / "openspec"
    openspec.mkdir(parents=True, exist_ok=True)
    (openspec / "spec.md").write_text("# spec\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_batch(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            batch_id="b1",
            batch_dir=tmp_path / "batch",
            status="completed",
            error=None,
        )

    monkeypatch.setattr("toyshop.pm.run_batch", fake_run_batch)

    state = DevGraphState(
        pm_root=tmp_path,
        project_name="demo",
        user_input="build demo",
    )
    out = run_dev_graph(state, llm=object())

    assert captured["allow_graph"] is False
    assert out.batch_id == "b1"
    assert out.spec_md == "# spec\n"
