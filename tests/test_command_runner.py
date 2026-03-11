from __future__ import annotations

from pathlib import Path

from toyshop.command_runner import run_command


def test_run_command_success(tmp_path: Path):
    result = run_command(["python3", "-c", "print('ok')"], cwd=tmp_path, timeout=10)
    assert result.returncode == 0
    assert "ok" in result.stdout
    assert result.timed_out is False


def test_run_command_timeout(tmp_path: Path):
    result = run_command(["python3", "-c", "import time; time.sleep(2)"], cwd=tmp_path, timeout=1)
    assert result.returncode == -1
    assert result.timed_out is True
    assert "timed out" in result.stderr
