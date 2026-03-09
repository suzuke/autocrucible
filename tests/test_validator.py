import subprocess
from pathlib import Path

import pytest

from crucible.validator import validate_project, CheckResult


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
