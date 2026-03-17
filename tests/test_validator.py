import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crucible.validator import validate_project, CheckResult, check_stability, StabilityResult, run_stability_check_and_update


VALID_CONFIG = """\
name: "test"
files:
  editable: ["solution.py"]
commands:
  run: "python3 solution.py > run.log 2>&1"
  eval: "grep '^metric:' run.log"
metric:
  name: "metric"
  direction: "minimize"
"""


def setup_valid_project(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(VALID_CONFIG)
    (cfg_dir / "program.md").write_text("Optimize the metric.")
    (tmp_path / "solution.py").write_text("print('metric: 0.5')")


def test_validate_all_pass(tmp_path):
    setup_valid_project(tmp_path)
    results = validate_project(tmp_path)
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_validate_missing_editable_file(tmp_path):
    setup_valid_project(tmp_path)
    (tmp_path / "solution.py").unlink()
    results = validate_project(tmp_path)
    file_check = [r for r in results if "editable" in r.name.lower()]
    assert any(not r.passed for r in file_check)


def test_validate_missing_program_md(tmp_path):
    setup_valid_project(tmp_path)
    (tmp_path / ".crucible" / "program.md").unlink()
    results = validate_project(tmp_path)
    prog_check = [r for r in results if "instructions" in r.name.lower()]
    assert any(not r.passed for r in prog_check)


def test_validate_run_command_fails(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    bad_config = VALID_CONFIG.replace(
        'run: "python3 solution.py > run.log 2>&1"',
        'run: "false"'
    )
    (cfg_dir / "config.yaml").write_text(bad_config)
    (cfg_dir / "program.md").write_text("Optimize.")
    (tmp_path / "solution.py").write_text("x = 1")
    results = validate_project(tmp_path)
    run_check = [r for r in results if "run command" in r.name.lower()]
    assert any(not r.passed for r in run_check)


def test_check_stability_stable(tmp_path):
    config = MagicMock()
    config.commands.run = "echo ok"
    config.commands.eval = "echo 'metric: 1.0'"
    config.metric.name = "metric"
    config.constraints.timeout_seconds = 30

    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        runner = MockRunner.return_value
        runner.execute.return_value = MagicMock(exit_code=0, timed_out=False)
        runner.parse_metric.return_value = 1.0
        result = check_stability(tmp_path, config, runs=5)

    assert result.stable is True
    assert result.cv == 0.0


def test_check_stability_unstable(tmp_path):
    config = MagicMock()
    config.constraints.timeout_seconds = 30

    values = iter([100.0, 200.0, 150.0, 300.0, 50.0])
    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        runner = MockRunner.return_value
        runner.execute.return_value = MagicMock(exit_code=0, timed_out=False)
        runner.parse_metric.side_effect = lambda *a: next(values)
        result = check_stability(tmp_path, config, runs=5)

    assert result.stable is False
    assert result.cv > 5.0


def test_check_stability_too_few_runs(tmp_path):
    config = MagicMock()
    config.constraints.timeout_seconds = 30

    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        runner = MockRunner.return_value
        runner.execute.return_value = MagicMock(exit_code=1, timed_out=False)
        result = check_stability(tmp_path, config, runs=3)

    assert result.stable is False
    assert result.reason == "too few successful runs"


CONFIG_WITH_ARTIFACTS = """\
name: "test"
files:
  editable: ["solution.py"]
  artifacts: ["data/"]
commands:
  run: "python3 solution.py > run.log 2>&1"
  eval: "grep '^metric:' run.log"
metric:
  name: "metric"
  direction: "minimize"
"""


CONFIG_WITH_OVERLAPPING_ARTIFACTS = """\
name: "test"
files:
  editable: ["solution.py"]
  artifacts: ["solution.py", "data/"]
commands:
  run: "python3 solution.py > run.log 2>&1"
  eval: "grep '^metric:' run.log"
metric:
  name: "metric"
  direction: "minimize"
"""


def test_validate_artifacts_no_overlap(tmp_path):
    """Artifacts that don't overlap with other categories pass validation."""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(CONFIG_WITH_ARTIFACTS)
    (cfg_dir / "program.md").write_text("Optimize the metric.")
    (tmp_path / "solution.py").write_text("print('metric: 0.5')")
    results = validate_project(tmp_path)
    artifact_checks = [r for r in results if r.name == "Artifacts"]
    assert len(artifact_checks) == 1
    assert artifact_checks[0].passed is True
    assert "data/" in artifact_checks[0].message


def _make_minimal_config_yaml(repeat=1):
    return (
        "name: test\nfiles:\n  editable: [sort.py]\n"
        "commands:\n  run: python sort.py\n  eval: python eval.py\n"
        "metric:\n  name: score\n  direction: maximize\n"
        f"evaluation:\n  repeat: {repeat}\n"
    )


def test_run_stability_check_stable(tmp_path):
    """Stable metric: no config change, returns stable result."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(_make_minimal_config_yaml())
    from crucible.config import load_config
    config = load_config(tmp_path)
    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        inst = MockRunner.return_value
        inst.execute.return_value = MagicMock(exit_code=0, timed_out=False)
        inst.parse_metric.return_value = 1.0
        result = run_stability_check_and_update(tmp_path, config, runs=3)
    assert result.stable is True
    # config.yaml should NOT have been modified to add repeat: 3
    updated = load_config(tmp_path)
    assert updated.evaluation.repeat == 1


def test_run_stability_check_unstable_writes_repeat(tmp_path):
    """Unstable metric (CV > 5%): auto-writes evaluation.repeat: 3 to config."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(_make_minimal_config_yaml())
    from crucible.config import load_config
    config = load_config(tmp_path)
    values = iter([1.0, 1.2, 0.8])  # CV ≈ 20%
    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        inst = MockRunner.return_value
        inst.execute.return_value = MagicMock(exit_code=0, timed_out=False)
        inst.parse_metric.side_effect = lambda *a: next(values)
        result = run_stability_check_and_update(tmp_path, config, runs=3)
    assert result.stable is False
    assert result.cv > 5.0
    updated = load_config(tmp_path)
    assert updated.evaluation.repeat == 3


def test_run_stability_check_writes_validated_marker(tmp_path):
    """Successful stability check writes .crucible/.validated marker."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(_make_minimal_config_yaml())
    from crucible.config import load_config
    config = load_config(tmp_path)
    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        inst = MockRunner.return_value
        inst.execute.return_value = MagicMock(exit_code=0, timed_out=False)
        inst.parse_metric.return_value = 1.0
        run_stability_check_and_update(tmp_path, config, runs=3)
    assert (tmp_path / ".crucible" / ".validated").exists()


def test_run_stability_check_already_repeat(tmp_path):
    """If repeat already > 1, skip stability check and return stable."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(_make_minimal_config_yaml(repeat=3))
    from crucible.config import load_config
    config = load_config(tmp_path)
    result = run_stability_check_and_update(tmp_path, config, runs=3)
    assert result.stable is True
    assert result.reason == "repeat already configured"


def test_validate_artifacts_overlap_with_editable(tmp_path):
    """Artifacts overlapping with editable files fail validation."""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(CONFIG_WITH_OVERLAPPING_ARTIFACTS)
    (cfg_dir / "program.md").write_text("Optimize the metric.")
    (tmp_path / "solution.py").write_text("print('metric: 0.5')")
    results = validate_project(tmp_path)
    artifact_checks = [r for r in results if r.name == "Artifacts"]
    assert len(artifact_checks) == 1
    assert artifact_checks[0].passed is False
    assert "solution.py" in artifact_checks[0].message
