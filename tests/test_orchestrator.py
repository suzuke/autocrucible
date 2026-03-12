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
    orch1.results.log(commit="abc1234", metric_value=0.5, status="keep", description="first")

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
