from __future__ import annotations

import json
from pathlib import Path

from toyshop.pm import create_batch, run_batch_tdd
from toyshop.tdd_pipeline import TDDResult


def _ok_tdd(summary: str) -> TDDResult:
    return TDDResult(
        success=True,
        whitebox_passed=True,
        blackbox_passed=True,
        summary=summary,
        whitebox_output="2 passed in 0.10s",
        blackbox_output="1 passed in 0.05s",
        files_created=["toyshop/new_file.py"],
        files_modified=["toyshop/existing.py"],
        test_files=["tests/test_auto.py"],
    )


def test_run_batch_tdd_writes_evidence_chain(monkeypatch, tmp_path: Path):
    batch = create_batch(tmp_path, "demo", "build auth", project_type="python")

    openspec = batch.batch_dir / "openspec"
    openspec.mkdir(parents=True, exist_ok=True)
    (openspec / "design.md").write_text("# design\n", encoding="utf-8")
    (openspec / "spec.md").write_text("# spec\n", encoding="utf-8")

    # Minimal workspace baseline to satisfy wiki/evidence logic
    ws = batch.batch_dir / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".toyshop").mkdir(parents=True, exist_ok=True)

    class FakeCodingAgent:
        def run_tdd(self, **kwargs):
            return _ok_tdd("ok")

    monkeypatch.setattr("toyshop.pm._run_pre_tdd_guard", lambda *args, **kwargs: None)

    result = run_batch_tdd(batch, llm=object(), coding_agent=FakeCodingAgent())
    assert result.success is True

    db_path = ws / ".toyshop" / "architecture.db"
    assert db_path.exists()

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        runs = conn.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC").fetchall()
        assert len(runs) >= 1
        run_id = runs[0]["id"]

        steps = conn.execute("SELECT * FROM process_steps WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        assert len(steps) >= 3

        gates = conn.execute("SELECT * FROM gate_results WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
        assert len(gates) >= 2

        diffs = conn.execute("SELECT * FROM code_diffs WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
        assert len(diffs) >= 2

        gate_types = {row["gate_type"] for row in gates}
        assert "whitebox" in gate_types
        assert "blackbox" in gate_types

        result_json = json.loads(runs[0]["result_json"])
        assert result_json["success"] is True
    finally:
        conn.close()


def test_run_batch_tdd_fails_when_completion_evidence_missing(monkeypatch, tmp_path: Path):
    batch = create_batch(tmp_path, "demo", "build auth", project_type="python")

    openspec = batch.batch_dir / "openspec"
    openspec.mkdir(parents=True, exist_ok=True)
    (openspec / "design.md").write_text("# design\n", encoding="utf-8")
    (openspec / "spec.md").write_text("# spec\n", encoding="utf-8")

    ws = batch.batch_dir / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".toyshop").mkdir(parents=True, exist_ok=True)

    class FakeCodingAgent:
        def run_tdd(self, **kwargs):
            return _ok_tdd("ok")

    def fake_validate(*args, **kwargs):
        raise ValueError("completion evidence guard failed")

    monkeypatch.setattr("toyshop.pm._run_pre_tdd_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr("toyshop.pm.validate_completion_evidence", fake_validate)

    result = run_batch_tdd(batch, llm=object(), coding_agent=FakeCodingAgent())
    assert result.success is False
    assert batch.status == "failed"
    assert "completion evidence guard failed" in (batch.error or "")
