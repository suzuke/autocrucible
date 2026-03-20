import json
import subprocess
from unittest.mock import patch
import pytest
from click.testing import CliRunner
from pathlib import Path
from crucible.cli import main
from crucible.results import results_filename, ExperimentRecord, _serialize_record


def _jsonl_line(commit, metric, status, desc):
    """Create a JSONL line for test results."""
    return _serialize_record(ExperimentRecord(
        commit=commit, metric_value=metric, status=status, description=desc,
    )) + "\n"


MOCK_ANALYZE = '{"inferred": {"name": "test", "metric_name": "score", "metric_direction": "maximize", "editable_files": ["solution.py"], "timeout_seconds": 60}, "uncertain": []}'
MOCK_GENERATE = '{"files": {".crucible/config.yaml": "name: test\\nfiles:\\n  editable: [solution.py]\\ncommands:\\n  run: \\"echo ok\\"\\n  eval: \\"echo score: 1\\"\\nmetric:\\n  name: score\\n  direction: maximize", ".crucible/program.md": "Optimize the score metric by modifying solution.py to maximize output.", "solution.py": "# Solution file for optimization\\nx = 1\\nprint(x)"}, "summary": "Test experiment"}'


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
    assert (tmp_path / results_filename("test1")).exists()


def test_status_command(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "test2", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["status", "--tag", "test2", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "0" in result.output


def test_verbose_flag(tmp_path):
    """--verbose flag is accepted and sets debug logging."""
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "vtest", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["--verbose", "status", "--tag", "vtest", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_run_resumes_existing_branch(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "test1", "--project-dir", str(tmp_path)])
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    result = runner.invoke(main, ["run", "--tag", "test1", "--project-dir", str(tmp_path)])
    assert "not found" not in (result.output or "").lower()


def test_status_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "json1", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["status", "--tag", "json1", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "total" in data
    assert "kept" in data
    assert "best" in data


def test_history_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "json2", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["history", "--tag", "json2", "--json", "--project-dir", str(tmp_path)])
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
    results_path = tmp_path / results_filename("a")
    with results_path.open("a") as f:
        f.write(_jsonl_line("abc1234", 0.5, "keep", "first improvement"))
    # Go back to main and create branch b
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    runner.invoke(main, ["init", "--tag", "b", "--project-dir", str(tmp_path)])
    results_path_b = tmp_path / results_filename("b")
    with results_path_b.open("a") as f:
        f.write(_jsonl_line("def5678", 0.3, "keep", "second improvement"))

    result = runner.invoke(main, ["compare", "a", "b", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "a" in result.output
    assert "b" in result.output


def test_compare_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "x", "--project-dir", str(tmp_path)])
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    runner.invoke(main, ["init", "--tag", "y", "--project-dir", str(tmp_path)])

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
    results_path = tmp_path / results_filename("pm1")
    with results_path.open("a") as f:
        f.write(_jsonl_line("abc1234", 0.5, "keep", "first improvement"))
        f.write(_jsonl_line("def5678", 0.6, "discard", "worse attempt"))
        f.write(_jsonl_line("ghi9012", 0.3, "keep", "big improvement"))
    result = runner.invoke(main, ["postmortem", "--tag", "pm1", "--no-ai", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Summary" in result.output
    assert "\u2588" in result.output


def test_postmortem_json(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "pm2", "--project-dir", str(tmp_path)])
    # Add some results
    results_path = tmp_path / results_filename("pm2")
    with results_path.open("a") as f:
        f.write(_jsonl_line("abc1234", 0.5, "keep", "first improvement"))
        f.write(_jsonl_line("def5678", 0.3, "keep", "second improvement"))
    result = runner.invoke(main, ["postmortem", "--tag", "pm2", "--no-ai", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "total" in data
    assert "kept" in data


def test_postmortem_no_results(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    # Don't init — no results file
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


def test_init_separate_tags_have_separate_results(tmp_path):
    """Each tag gets its own results file — no overwriting."""
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    # Add a result to run1
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write(_jsonl_line("abc1234", 0.5, "keep", "first"))
    # Go back to main and init run2
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    runner.invoke(main, ["init", "--tag", "run2", "--project-dir", str(tmp_path)])
    # run1's results should still exist
    assert (tmp_path / results_filename("run1")).exists()
    run1_content = (tmp_path / results_filename("run1")).read_text()
    assert "abc1234" in run1_content
    # run2 should have its own empty results
    assert (tmp_path / results_filename("run2")).exists()


def test_run_auto_inits_when_no_branch(tmp_path):
    """run auto-initialises when tag branch doesn't exist yet."""
    setup_project(tmp_path)
    runner = CliRunner()
    # run without init — should auto-init and create results file
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(main, ["run", "--tag", "auto1", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Initialised experiment" in result.output
    assert (tmp_path / results_filename("auto1")).exists()


def test_run_shows_fork_menu_when_previous_runs_exist(tmp_path):
    """When previous results exist, run should show fork menu."""
    setup_project(tmp_path)
    runner = CliRunner()
    # Create run1 with results
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write(_jsonl_line("abc1234", 0.5, "keep", "first improvement"))
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    # Run run2 — user selects "Start fresh" (option 2)
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--project-dir", str(tmp_path)],
            input="2\n",  # "Start fresh"
        )
    assert result.exit_code == 0, result.output
    assert "run1" in result.output


def test_run_fork_from_previous(tmp_path):
    """Selecting a previous run forks from its best commit."""
    setup_project(tmp_path)
    runner = CliRunner()
    # Create run1 with a commit and results
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    (tmp_path / "train.py").write_text("x = 2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "improvement"], cwd=tmp_path, check=True, capture_output=True)
    best_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write(_jsonl_line(best_commit, 0.5, "keep", "improvement"))
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    # Run run2 — user selects run1 (option 1)
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--project-dir", str(tmp_path)],
            input="1\n",
        )
    assert result.exit_code == 0, result.output
    assert "fork" in result.output.lower() or "Forking" in result.output
    # Verify baseline was seeded
    results_run2 = tmp_path / results_filename("run2")
    assert results_run2.exists()
    content = results_run2.read_text()
    assert "baseline" in content


def test_run_no_interactive_skips_menu(tmp_path):
    """--no-interactive skips the fork menu."""
    setup_project(tmp_path)
    runner = CliRunner()
    # Create run1 with results
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write(_jsonl_line("abc1234", 0.5, "keep", "improvement"))
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--no-interactive", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    # Should not prompt — just start fresh
    assert "Initialised" in result.output


def test_run_no_menu_when_no_previous_runs(tmp_path):
    """No menu when there are no previous results files."""
    setup_project(tmp_path)
    runner = CliRunner()
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run1", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    assert "Initialised" in result.output


def test_fork_baseline_full_flow(tmp_path):
    """End-to-end: run1 produces results, run2 forks from run1's best."""
    setup_project(tmp_path)
    runner = CliRunner()

    # === Run1: init and add results ===
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    # Simulate agent making an improvement
    (tmp_path / "train.py").write_text("x = optimized")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "optimize"], cwd=tmp_path, check=True, capture_output=True)
    best_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write(_jsonl_line(best_commit, 0.3, "keep", "optimized x"))

    # Go back to main
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    # === Run2: fork from run1 ===
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--project-dir", str(tmp_path)],
            input="1\n",  # Select run1
        )
    assert result.exit_code == 0, result.output

    # Verify: run2 branch has run1's code
    assert (tmp_path / "train.py").read_text() == "x = optimized"

    # Verify: run2 results have baseline
    from crucible.results import ResultsLog
    log2 = ResultsLog(tmp_path / results_filename("run2"))
    records = log2.read_all()
    assert len(records) == 1
    assert records[0].status == "baseline"
    assert records[0].metric_value == 0.3

    # Verify: is_improvement uses baseline as threshold
    assert log2.is_improvement(0.29, "minimize") is True
    assert log2.is_improvement(0.31, "minimize") is False


def test_run_auto_inits_git_repo(tmp_path):
    """run auto-initialises git repo when .git is missing."""
    # Create project without git
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(VALID_CONFIG)
    (cfg_dir / "program.md").write_text("You are a researcher.")
    (tmp_path / "train.py").write_text("x = 1")

    runner = CliRunner()
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(main, ["run", "--tag", "auto2", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "No git repo found" in result.output
    assert "Git repo initialized" in result.output
    assert (tmp_path / ".git").exists()
    assert (tmp_path / results_filename("auto2")).exists()


def test_run_max_iterations_flag(tmp_path):
    """--max-iterations flag is passed to run_loop."""
    setup_project(tmp_path)
    runner = CliRunner()
    with patch("crucible.orchestrator.Orchestrator.run_loop") as mock_loop:
        result = runner.invoke(
            main,
            ["run", "--tag", "mi1", "--max-iterations", "5", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    mock_loop.assert_called_once_with(max_iterations=5)


def test_run_without_max_iterations_passes_none(tmp_path):
    """Without --max-iterations, run_loop is called with max_iterations=None."""
    setup_project(tmp_path)
    runner = CliRunner()
    with patch("crucible.orchestrator.Orchestrator.run_loop") as mock_loop:
        result = runner.invoke(
            main,
            ["run", "--tag", "mi2", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    mock_loop.assert_called_once_with(max_iterations=None)


# ── update command tests ──────────────────────────────────────


def test_update_already_up_to_date():
    """When current == latest, show 'already up to date'."""
    runner = CliRunner()
    with (
        patch("crucible.cli._get_current_version", return_value="0.5.3"),
        patch("crucible.cli._get_latest_version", return_value="0.5.3"),
    ):
        result = runner.invoke(main, ["update"])
    assert result.exit_code == 0
    assert "Already up to date (v0.5.3)" in result.output


def test_update_check_shows_available():
    """--check shows available update without installing."""
    runner = CliRunner()
    with (
        patch("crucible.cli._get_current_version", return_value="0.5.3"),
        patch("crucible.cli._get_latest_version", return_value="0.6.0"),
    ):
        result = runner.invoke(main, ["update", "--check"])
    assert result.exit_code == 0
    assert "v0.5.3" in result.output
    assert "v0.6.0" in result.output
    assert "Run 'crucible update' to install" in result.output


def test_update_check_already_latest():
    """--check when already up to date."""
    runner = CliRunner()
    with (
        patch("crucible.cli._get_current_version", return_value="0.5.3"),
        patch("crucible.cli._get_latest_version", return_value="0.5.3"),
    ):
        result = runner.invoke(main, ["update", "--check"])
    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_update_network_failure():
    """When PyPI is unreachable, show error."""
    runner = CliRunner()
    with (
        patch("crucible.cli._get_current_version", return_value="0.5.3"),
        patch("crucible.cli._get_latest_version", return_value=None),
    ):
        result = runner.invoke(main, ["update"])
    assert result.exit_code != 0
    assert "Failed to check PyPI" in result.output


def test_update_runs_uv_upgrade():
    """When update available, runs uv tool upgrade."""
    runner = CliRunner()
    with (
        patch("crucible.cli._get_current_version", return_value="0.5.3"),
        patch("crucible.cli._get_latest_version", return_value="0.6.0"),
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = runner.invoke(main, ["update"])
    assert result.exit_code == 0
    assert "v0.6.0 ✓" in result.output
    mock_run.assert_called_once_with(
        ["uv", "tool", "upgrade", "autocrucible"],
        capture_output=True,
        text=True,
    )


def test_update_uv_not_found():
    """When uv is not installed, show helpful error."""
    runner = CliRunner()
    with (
        patch("crucible.cli._get_current_version", return_value="0.5.3"),
        patch("crucible.cli._get_latest_version", return_value="0.6.0"),
        patch("shutil.which", return_value=None),
    ):
        result = runner.invoke(main, ["update"])
    assert result.exit_code != 0
    assert "uv is required" in result.output
