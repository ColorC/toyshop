from __future__ import annotations

from pathlib import Path

from toyshop.adapters.reporting import FileReportingAdapter


def test_file_reporting_adapter_writes_mid_report(tmp_path: Path):
    adapter = FileReportingAdapter()
    payload = {
        "run_id": "r1",
        "checkpoint": "mvp_uploaded",
        "summary": "ok",
    }

    adapter.publish(payload, run_dir=tmp_path)

    output = tmp_path / "mid_report_hook.json"
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert '"run_id": "r1"' in text
    assert '"checkpoint": "mvp_uploaded"' in text
