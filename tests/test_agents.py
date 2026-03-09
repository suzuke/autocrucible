import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from crucible.agents.base import AgentInterface, AgentResult
from crucible.agents.claude_code import ClaudeCodeAgent


def test_agent_result_dataclass():
    r = AgentResult(modified_files=[Path("train.py")], description="test change")
    assert r.description == "test change"
    assert r.modified_files == [Path("train.py")]


def test_claude_code_agent_generate_edit(tmp_path):
    """Test with mocked claude CLI call."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    agent = ClaudeCodeAgent()

    real_run = subprocess.run

    def mock_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "claude" in cmd_str:
            (tmp_path / "train.py").write_text("x = 2  # optimized")
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "Changed x to 2 for better performance"
            mock.stderr = ""
            return mock
        return real_run(*args, **kwargs)

    with patch("crucible.agents.claude_code.subprocess.run", side_effect=mock_run):
        result = agent.generate_edit("optimize x", tmp_path)

    assert Path("train.py") in result.modified_files
    assert len(result.description) > 0
