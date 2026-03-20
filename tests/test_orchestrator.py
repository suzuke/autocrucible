import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from crucible.orchestrator import Orchestrator
from crucible.config import (
    Config, FilesConfig, CommandsConfig, MetricConfig,
    ConstraintsConfig, AgentConfig, ContextWindowConfig, GitConfig,
)
from crucible.agents.base import AgentResult
from crucible.results import ExperimentRecord


def make_config():
    return Config(
        name="test",
        files=FilesConfig(editable=["train.py"], readonly=["prepare.py"]),
        commands=CommandsConfig(run="python train.py > run.log 2>&1", eval="grep '^loss:' run.log"),
        metric=MetricConfig(name="loss", direction="minimize"),
        constraints=ConstraintsConfig(timeout_seconds=60, max_retries=2),
        agent=AgentConfig(context_window=ContextWindowConfig()),
        git=GitConfig(),
    )


def setup_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    (tmp_path / "prepare.py").write_text("# readonly")
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "program.md").write_text("You are a researcher.")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_single_successful_iteration(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()

    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    # Mock runner to simulate successful experiment
    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95

        def modify_file(*args, **kwargs):
            (tmp_path / "train.py").write_text("x = 2")
            return AgentResult(modified_files=[Path("train.py")], description="optimize x")
        mock_agent.generate_edit.side_effect = modify_file

        result = orch.run_one_iteration()

    assert result == "keep"
    records = orch.results.read_all()
    assert len(records) == 1
    assert records[0].status == "keep"


def test_iteration_with_crash(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()

    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=1, timed_out=False, stderr_tail="OOM error")
        mock_parse.return_value = None

        def modify_file(*args, **kwargs):
            (tmp_path / "train.py").write_text("x = bad")
            return AgentResult(modified_files=[Path("train.py")], description="bad change")
        mock_agent.generate_edit.side_effect = modify_file

        result = orch.run_one_iteration()

    assert result == "crash"


def test_iteration_with_readonly_violation(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()

    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def modify_readonly(*args, **kwargs):
        (tmp_path / "prepare.py").write_text("# hacked")
        return AgentResult(modified_files=[Path("prepare.py")], description="bad edit")
    mock_agent.generate_edit.side_effect = modify_readonly

    result = orch.run_one_iteration()
    assert result == "violation"


def test_resume_existing_branch(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()

    orch1 = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch1.init()
    orch1.results.log(ExperimentRecord(commit="abc1234", metric_value=0.5, status="keep", description="first"))

    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    orch2 = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch2.resume()

    result = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, capture_output=True, text=True)
    assert result.stdout.strip() == "crucible/test"

    records = orch2.results.read_all()
    assert len(records) == 1
    assert records[0].metric_value == 0.5


def test_hidden_files_remain_on_disk_during_agent_call(tmp_path):
    """Hidden files stay on disk (protected via SDK can_use_tool, not filesystem moves)."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.files.hidden = ["secret.py"]

    (tmp_path / "secret.py").write_text("SECRET = 42")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add secret"], cwd=tmp_path, check=True, capture_output=True)

    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    seen_during_agent = {}

    def agent_check_files(*args, **kwargs):
        seen_during_agent["secret_exists"] = (tmp_path / "secret.py").exists()
        (tmp_path / "train.py").write_text("x = 2")
        return AgentResult(modified_files=[Path("train.py")], description="edit")
    mock_agent.generate_edit.side_effect = agent_check_files

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95
        result = orch.run_one_iteration()

    assert result == "keep"
    # Files remain on disk — protection is at SDK level, not filesystem
    assert seen_during_agent["secret_exists"] is True
    assert (tmp_path / "secret.py").exists()


def test_hidden_files_stripped_from_modified_list(tmp_path):
    """Hidden files reported as modified by agent are stripped from the list."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.files.hidden = ["secret.py"]

    (tmp_path / "secret.py").write_text("SECRET = 42")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add secret"], cwd=tmp_path, check=True, capture_output=True)

    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def agent_creates_hidden(*args, **kwargs):
        (tmp_path / "train.py").write_text("x = 5")
        return AgentResult(
            modified_files=[Path("secret.py"), Path("train.py")],
            description="edit with hidden file",
        )
    mock_agent.generate_edit.side_effect = agent_creates_hidden

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95
        result = orch.run_one_iteration()

    assert result == "keep"


def test_hidden_file_only_edit_becomes_skip(tmp_path):
    """If agent only modifies hidden files (no editable changes), result is 'skip'."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.files.hidden = ["secret.py"]

    (tmp_path / "secret.py").write_text("SECRET = 42")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add secret"], cwd=tmp_path, check=True, capture_output=True)

    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def agent_only_hidden(*args, **kwargs):
        return AgentResult(
            modified_files=[Path("secret.py")],
            description="only hidden file",
        )
    mock_agent.generate_edit.side_effect = agent_only_hidden

    result = orch.run_one_iteration()
    assert result == "skip"


def test_init_with_fork_from(tmp_path):
    """init() with fork_from creates branch from specified commit and seeds baseline."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()

    # Make a second commit (simulating work done in run1)
    (tmp_path / "train.py").write_text("x = 2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "run1 best"], cwd=tmp_path, check=True, capture_output=True)
    best_commit_full = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()

    orch = Orchestrator(cfg, tmp_path, tag="run2", agent=mock_agent)
    orch.init(fork_from=(best_commit_full, 600.0, "run1"))

    # Should be on run2 branch
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path, capture_output=True, text=True,
    ).stdout.strip()
    assert branch == "crucible/run2"

    # Should have baseline record
    records = orch.results.read_all()
    assert len(records) == 1
    assert records[0].status == "baseline"
    assert records[0].metric_value == 600.0

    # Code state should match the forked commit
    assert (tmp_path / "train.py").read_text() == "x = 2"


def test_init_without_fork_from_unchanged(tmp_path):
    """init() without fork_from works exactly as before."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="run1", agent=mock_agent)
    orch.init()

    records = orch.results.read_all()
    assert len(records) == 0  # No baseline

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path, capture_output=True, text=True,
    ).stdout.strip()
    assert branch == "crucible/run1"


def test_budget_exceeded_stops(tmp_path):
    """run_one_iteration returns budget_exceeded when cost exceeds limit."""
    from crucible.budget import BudgetConfig
    from crucible.results import UsageInfo

    setup_repo(tmp_path)
    cfg = make_config()
    cfg.constraints.budget = BudgetConfig(max_cost_usd=0.01)
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def modify_file(*args, **kwargs):
        (tmp_path / "train.py").write_text("x = 2")
        return AgentResult(
            modified_files=[Path("train.py")], description="optimize",
            usage=UsageInfo(total_cost_usd=0.02),
        )
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95
        result = orch.run_one_iteration()

    assert result == "budget_exceeded"


def test_budget_warning_does_not_stop(tmp_path):
    """run_one_iteration continues when budget is at warning level."""
    from crucible.budget import BudgetConfig
    from crucible.results import UsageInfo

    setup_repo(tmp_path)
    cfg = make_config()
    cfg.constraints.budget = BudgetConfig(max_cost_usd=1.00, warn_at_percent=80)
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def modify_file(*args, **kwargs):
        (tmp_path / "train.py").write_text("x = 2")
        return AgentResult(
            modified_files=[Path("train.py")], description="optimize",
            usage=UsageInfo(total_cost_usd=0.85),
        )
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95
        result = orch.run_one_iteration()

    # Should keep the result, not stop
    assert result == "keep"
    # Budget should be at warning level
    assert orch.budget.percent_used >= 80


def test_no_budget_config_runs_normally(tmp_path):
    """Without budget config, iterations run without budget checks."""
    from crucible.results import UsageInfo

    setup_repo(tmp_path)
    cfg = make_config()
    # budget defaults to None in ConstraintsConfig
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def modify_file(*args, **kwargs):
        (tmp_path / "train.py").write_text("x = 2")
        return AgentResult(
            modified_files=[Path("train.py")], description="optimize",
            usage=UsageInfo(total_cost_usd=100.0),
        )
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95
        result = orch.run_one_iteration()

    assert result == "keep"


def test_sandbox_runner_used_when_configured(tmp_path):
    """Orchestrator uses SandboxRunner when sandbox config has a non-'none' backend."""
    from crucible.config import SandboxConfig
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.sandbox = SandboxConfig(backend="docker", base_image="python:3.12-slim")
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()
    from crucible.sandbox import SandboxRunner
    assert isinstance(orch.runner, SandboxRunner)


def test_no_sandbox_uses_native_runner(tmp_path):
    """Without sandbox config, orchestrator uses the native ExperimentRunner."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()
    from crucible.runner import ExperimentRunner
    assert isinstance(orch.runner, ExperimentRunner)


def test_allow_install_adds_requirements_to_editable(tmp_path):
    """allow_install adds requirements.txt to editable set and touches the file."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.constraints.allow_install = True
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()
    assert "requirements.txt" in orch.guardrails.editable
    assert (tmp_path / "requirements.txt").exists()


def test_iteration_saves_agent_log(tmp_path):
    """Agent output is saved to logs/iter-N/agent.txt."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def modify_file(*args, **kwargs):
        (tmp_path / "train.py").write_text("x = 2")
        return AgentResult(
            modified_files=[Path("train.py")],
            description="optimize x",
            agent_output="I read the code.\nDecided to change x to 2.",
        )
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95
        orch.run_one_iteration()

    agent_log = tmp_path / "logs" / "iter-1" / "agent.txt"
    assert agent_log.exists()
    assert "Decided to change x to 2" in agent_log.read_text()


def test_allow_install_installs_on_requirements_change(tmp_path):
    """When requirements.txt is modified and allow_install is true, pip install runs."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.constraints.allow_install = True
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    def modify_files(*args, **kwargs):
        (tmp_path / "train.py").write_text("x = 2")
        (tmp_path / "requirements.txt").write_text("numpy\n")
        return AgentResult(
            modified_files=[Path("train.py"), Path("requirements.txt")],
            description="add numpy",
        )
    mock_agent.generate_edit.side_effect = modify_files

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse, \
         patch.object(orch, "_install_requirements") as mock_install:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95
        result = orch.run_one_iteration()

    assert result == "keep"
    mock_install.assert_called_once()


def test_install_requirements_uses_venv_env(tmp_path):
    """_install_requirements uses python3 -m pip with .venv PATH."""
    setup_repo(tmp_path)
    (tmp_path / "requirements.txt").write_text("numpy\n")
    # Create a .venv so runner._make_env() will inject it
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)

    cfg = make_config()
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)

    with patch("crucible.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        orch._install_requirements()

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    # Must use python3 -m pip, not bare pip
    assert args[0] == ["python3", "-m", "pip", "install", "-r", str(tmp_path / "requirements.txt")]
    # Must inject .venv into PATH
    assert str(venv_bin) in kwargs["env"]["PATH"].split(":")[0]
    assert kwargs["env"]["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_install_requirements_no_venv_still_uses_python3_m_pip(tmp_path):
    """_install_requirements uses python3 -m pip even without .venv."""
    setup_repo(tmp_path)
    (tmp_path / "requirements.txt").write_text("requests\n")

    cfg = make_config()
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)

    with patch("crucible.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        orch._install_requirements()

    args, _ = mock_run.call_args
    assert args[0][:3] == ["python3", "-m", "pip"]


def test_init_creates_artifacts_dirs_and_gitignores(tmp_path):
    """init() creates artifact directories and adds them to .gitignore."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.files.artifacts = ["data/", "checkpoints/"]
    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    # Artifact directories should be created
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "checkpoints").is_dir()

    # Artifact paths should be in .gitignore
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "data/" in gitignore
    assert "checkpoints/" in gitignore


def test_run_loop_stops_at_max_iterations(tmp_path):
    """run_loop stops after max_iterations iterations."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    call_count = 0

    def modify_file(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        (tmp_path / "train.py").write_text(f"x = {call_count + 100}")
        return AgentResult(modified_files=[Path("train.py")], description=f"iter {call_count}")
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        # Return decreasing values so each iteration is "keep"
        mock_parse.side_effect = [0.5, 0.4, 0.3]
        orch.run_loop(max_iterations=3)

    assert call_count == 3


def test_run_loop_none_max_iterations_uses_config(tmp_path):
    """run_loop with no explicit max_iterations falls back to config value."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.constraints.max_iterations = 2
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    call_count = 0

    def modify_file(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        (tmp_path / "train.py").write_text(f"x = {call_count + 100}")
        return AgentResult(modified_files=[Path("train.py")], description=f"iter {call_count}")
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.side_effect = [0.5, 0.4]
        orch.run_loop()  # No explicit max_iterations — should use config

    assert call_count == 2
