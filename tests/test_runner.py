import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
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


def test_parse_metric_custom_timeout(tmp_path):
    """parse_metric respects the timeout parameter."""
    runner = ExperimentRunner(workspace=tmp_path)
    # Command that takes longer than default 30s timeout but within custom timeout
    value = runner.parse_metric("echo 'score: 42.5'", "score", timeout=5)
    assert value == pytest.approx(42.5)


def test_parse_metric_timeout_exceeded(tmp_path):
    """parse_metric returns None when command exceeds timeout."""
    runner = ExperimentRunner(workspace=tmp_path)
    value = runner.parse_metric("sleep 10 && echo 'score: 42.5'", "score", timeout=1)
    assert value is None


def test_stderr_tail(tmp_path):
    runner = ExperimentRunner(workspace=tmp_path)
    result = runner.execute("python3 -c 'raise ValueError(\"boom\")'", timeout=10)
    assert "boom" in result.stderr_tail


# --- execute_with_repeat tests ---


@pytest.mark.parametrize("aggregation", ["median", "mean"])
def test_execute_with_repeat_aggregation(tmp_path, aggregation):
    """Both median and mean of [1, 3, 2] return 2.0."""
    runner = ExperimentRunner(workspace=tmp_path)
    ok = RunResult(exit_code=0, timed_out=False)
    metrics = iter([1.0, 3.0, 2.0])

    with patch.object(runner, "execute", return_value=ok), \
         patch.object(runner, "parse_metric", side_effect=lambda *a: next(metrics)):
        result, value = runner.execute_with_repeat(
            "run", "eval", "m", repeat=3, aggregation=aggregation, timeout=10,
        )
    assert value == 2.0


def test_execute_with_repeat_failure_returns_none(tmp_path):
    """If any run fails, return None metric."""
    runner = ExperimentRunner(workspace=tmp_path)
    fail = RunResult(exit_code=1, timed_out=False)

    with patch.object(runner, "execute", return_value=fail):
        result, value = runner.execute_with_repeat(
            "run", "eval", "m", repeat=3, aggregation="median", timeout=10,
        )
    assert value is None


def test_execute_with_repeat_one(tmp_path):
    """repeat=1 works like a normal single run."""
    runner = ExperimentRunner(workspace=tmp_path)
    ok = RunResult(exit_code=0, timed_out=False)

    with patch.object(runner, "execute", return_value=ok), \
         patch.object(runner, "parse_metric", return_value=42.0):
        result, value = runner.execute_with_repeat(
            "run", "eval", "m", repeat=1, aggregation="median", timeout=10,
        )
    assert value == 42.0


def test_execute_with_repeat_metric_parse_fails(tmp_path):
    """If metric parsing fails on any run, return None."""
    runner = ExperimentRunner(workspace=tmp_path)
    ok = RunResult(exit_code=0, timed_out=False)

    with patch.object(runner, "execute", return_value=ok), \
         patch.object(runner, "parse_metric", return_value=None):
        result, value = runner.execute_with_repeat(
            "run", "eval", "m", repeat=3, aggregation="median", timeout=10,
        )
    assert value is None
