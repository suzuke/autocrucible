import subprocess
import pytest
from click.testing import CliRunner
from pathlib import Path
from crucible.cli import main


VALID_CONFIG = """\
name: "test"
files:
  editable: ["train.py"]
commands:
  run: "echo 'loss: 0.5' > run.log"
  eval: "grep '^loss:' run.log"
metric:
  name: "loss"
  direction: "minimize"
"""


def setup_project(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(VALID_CONFIG)
    (cfg_dir / "program.md").write_text("You are a researcher.")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_init_command(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--tag", "test1", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "results.tsv").exists()


def test_status_command(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "test2", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "0" in result.output


def test_verbose_flag(tmp_path):
    """--verbose flag is accepted and sets debug logging."""
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "vtest", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["--verbose", "status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_run_resumes_existing_branch(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "test1", "--project-dir", str(tmp_path)])
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    result = runner.invoke(main, ["run", "--tag", "test1", "--project-dir", str(tmp_path)])
    assert "No results.tsv found" not in (result.output or "")


def test_init_missing_config(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--tag", "test3", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0
