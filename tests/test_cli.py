import json
import subprocess
from unittest.mock import patch
import pytest
from click.testing import CliRunner
from pathlib import Path
from crucible.cli import main


MOCK_ANALYZE = '{"inferred": {"name": "test", "metric_name": "score", "metric_direction": "maximize", "editable_files": ["solution.py"], "timeout_seconds": 60}, "uncertain": []}'
MOCK_GENERATE = '{"files": {".crucible/config.yaml": "name: test\\nfiles:\\n  editable: [solution.py]\\ncommands:\\n  run: \\"echo ok\\"\\n  eval: \\"echo score: 1\\"\\nmetric:\\n  name: score\\n  direction: maximize", ".crucible/program.md": "Optimize.", "solution.py": "x = 1"}, "summary": "Test experiment"}'


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


def test_status_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "json1", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["status", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "total" in data
    assert "kept" in data
    assert "best" in data


def test_history_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "json2", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["history", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_validate_command(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(VALID_CONFIG)
    (cfg_dir / "program.md").write_text("Optimize.")
    (tmp_path / "train.py").write_text("print('loss: 0.5')")
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_compare_command(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    # Create branch a with a result
    runner.invoke(main, ["init", "--tag", "a", "--project-dir", str(tmp_path)])
    results_path = tmp_path / "results.tsv"
    with results_path.open("a") as f:
        f.write("abc1234\t0.5\tkeep\tfirst improvement\n")
    subprocess.run(["git", "add", "-f", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)
    # Go back to main and create branch b
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    runner.invoke(main, ["init", "--tag", "b", "--project-dir", str(tmp_path)])
    with results_path.open("a") as f:
        f.write("def5678\t0.3\tkeep\tsecond improvement\n")
    subprocess.run(["git", "add", "-f", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)

    result = runner.invoke(main, ["compare", "a", "b", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "a" in result.output
    assert "b" in result.output


def test_compare_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "x", "--project-dir", str(tmp_path)])
    subprocess.run(["git", "add", "-f", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    runner.invoke(main, ["init", "--tag", "y", "--project-dir", str(tmp_path)])
    subprocess.run(["git", "add", "-f", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)

    result = runner.invoke(main, ["compare", "x", "y", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "x" in data
    assert "y" in data


def test_postmortem_no_ai(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "pm1", "--project-dir", str(tmp_path)])
    # Add some results
    results_path = tmp_path / "results.tsv"
    with results_path.open("a") as f:
        f.write("abc1234\t0.5\tkeep\tfirst improvement\n")
        f.write("def5678\t0.6\tdiscard\tworse attempt\n")
        f.write("ghi9012\t0.3\tkeep\tbig improvement\n")
    result = runner.invoke(main, ["postmortem", "--tag", "pm1", "--no-ai", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Summary" in result.output
    assert "\u2588" in result.output


def test_postmortem_json(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "pm2", "--project-dir", str(tmp_path)])
    # Add some results
    results_path = tmp_path / "results.tsv"
    with results_path.open("a") as f:
        f.write("abc1234\t0.5\tkeep\tfirst improvement\n")
        f.write("def5678\t0.3\tkeep\tsecond improvement\n")
    result = runner.invoke(main, ["postmortem", "--tag", "pm2", "--no-ai", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "total" in data
    assert "kept" in data


def test_postmortem_no_results(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    # Don't init — no results.tsv
    result = runner.invoke(main, ["postmortem", "--tag", "pm3", "--no-ai", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_init_missing_config(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--tag", "test3", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0


@patch("crucible.wizard._call_claude", side_effect=[MOCK_ANALYZE, MOCK_GENERATE])
def test_wizard_command_with_describe(mock_claude, tmp_path):
    runner = CliRunner()
    dest = str(tmp_path / "my-exp")
    result = runner.invoke(main, ["wizard", dest, "--describe", "optimize a neural network"])
    assert result.exit_code == 0, result.output
    dest_path = Path(dest)
    assert (dest_path / ".crucible" / "config.yaml").exists()
    assert (dest_path / ".crucible" / "program.md").exists()
    assert (dest_path / "solution.py").exists()
    assert (dest_path / "pyproject.toml").exists()


@patch("crucible.wizard._call_claude", side_effect=[MOCK_ANALYZE, MOCK_GENERATE])
def test_wizard_command_interactive(mock_claude, tmp_path):
    runner = CliRunner()
    dest = str(tmp_path / "my-exp")
    result = runner.invoke(main, ["wizard", dest], input="optimize something\n")
    assert result.exit_code == 0, result.output
    dest_path = Path(dest)
    assert (dest_path / ".crucible" / "config.yaml").exists()
    assert (dest_path / "solution.py").exists()


def test_wizard_help():
    runner = CliRunner()
    result = runner.invoke(main, ["wizard", "--help"])
    assert result.exit_code == 0
    assert "description" in result.output.lower() or "natural language" in result.output.lower()


def test_postmortem_help():
    runner = CliRunner()
    result = runner.invoke(main, ["postmortem", "--help"])
    assert result.exit_code == 0
    assert "--no-ai" in result.output
    assert "--json" in result.output
