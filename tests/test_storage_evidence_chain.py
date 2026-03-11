from __future__ import annotations

from toyshop.storage.database import (
    init_database,
    close_database,
    create_project,
    create_workflow_run,
    complete_workflow_run,
    append_process_step,
    save_code_diff,
    save_gate_result,
    get_process_steps,
    get_code_diffs,
    get_gate_results,
    validate_completion_evidence,
    delete_project,
)


def test_execution_evidence_chain_roundtrip(tmp_path):
    db_path = tmp_path / "evidence.db"
    init_database(db_path)
    try:
        proj = create_project("evidence-project", str(tmp_path))
        run = create_workflow_run(proj["id"], "tdd_create", batch_id="b1")

        step = append_process_step(
            run["id"],
            seq=1,
            stage="coding",
            action="run_tdd",
            status="success",
            agent_id="coding_agent",
            reason_ref={"batch_id": "b1"},
        )
        save_code_diff(run["id"], step["id"], "toyshop/foo.py", added=10, deleted=2)
        save_gate_result(
            run["id"],
            step["id"],
            gate_type="whitebox",
            passed=True,
            report={"passed": 12, "failed": 0},
        )

        complete_workflow_run(run["id"], "completed", {"success": True})

        steps = get_process_steps(run["id"])
        diffs = get_code_diffs(run["id"])
        gates = get_gate_results(run["id"])

        assert len(steps) == 1
        assert steps[0]["reason_ref"]["batch_id"] == "b1"
        assert len(diffs) == 1
        assert diffs[0]["file_path"] == "toyshop/foo.py"
        assert len(gates) == 1
        assert gates[0]["gate_type"] == "whitebox"
        assert gates[0]["passed"] is True
        assert gates[0]["report"]["passed"] == 12
    finally:
        close_database()


def test_delete_project_cascades_execution_evidence(tmp_path):
    db_path = tmp_path / "evidence.db"
    init_database(db_path)
    try:
        proj = create_project("delete-project", str(tmp_path))
        run = create_workflow_run(proj["id"], "tdd_modify", batch_id="b2")
        step = append_process_step(run["id"], 1, "testing", "whitebox_gate", "failed")
        save_code_diff(run["id"], step["id"], "toyshop/bar.py")
        save_gate_result(run["id"], step["id"], "whitebox", False, report={"failed": 1})

        delete_project(proj["id"])

        assert get_process_steps(run["id"]) == []
        assert get_code_diffs(run["id"]) == []
        assert get_gate_results(run["id"]) == []
    finally:
        close_database()


def test_validate_completion_evidence_missing_parts(tmp_path):
    db_path = tmp_path / "evidence.db"
    init_database(db_path)
    try:
        proj = create_project("guard-project", str(tmp_path))
        run = create_workflow_run(proj["id"], "tdd_create", batch_id="b3")
        ok, missing = validate_completion_evidence(run["id"])
        assert ok is False
        assert "process_steps" in missing
        assert "code_diffs" in missing
        assert "gate_results" in missing

        step = append_process_step(run["id"], 1, "coding", "run_tdd", "success")
        save_code_diff(run["id"], step["id"], "toyshop/a.py")
        save_gate_result(run["id"], step["id"], "whitebox", True)

        ok2, missing2 = validate_completion_evidence(run["id"])
        assert ok2 is False
        assert "blackbox_gate" in missing2
    finally:
        close_database()
