import pytest
from pathlib import Path
from crucible.runner import ExperimentRunner, RunResult


def test_run_successful_command(tmp_path):
    runner = ExperimentRunner(workspace=tmp_path)
    result = runner.execute("echo 'hello'", timeout=10)
    assert result.exit_code == 0
    assert result.timed_out is False


def test_run_timeout(tmp_path):
    runner = ExperimentRunner(workspace=tmp_path)
    result = runner.execute("sleep 30", timeout=2)
    assert result.timed_out is True


def test_run_failing_command(tmp_path):
    runner = ExperimentRunner(workspace=tmp_path)
    result = runner.execute("exit 1", timeout=10)
    assert result.exit_code != 0
    assert result.timed_out is False


def test_parse_metric(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("some output\nval_bpb: 0.997900\npeak_vram_mb: 45060.2\n")
    runner = ExperimentRunner(workspace=tmp_path)
    value = runner.parse_metric(f"grep '^val_bpb:' {log}", "val_bpb")
    assert value == pytest.approx(0.9979)


def test_parse_metric_missing(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("crash traceback here\n")
    runner = ExperimentRunner(workspace=tmp_path)
    value = runner.parse_metric(f"grep '^val_bpb:' {log}", "val_bpb")
    assert value is None


def test_stderr_tail(tmp_path):
    runner = ExperimentRunner(workspace=tmp_path)
    result = runner.execute("python3 -c 'raise ValueError(\"boom\")'", timeout=10)
    assert "boom" in result.stderr_tail
