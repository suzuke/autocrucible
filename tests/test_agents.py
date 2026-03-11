import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from crucible.agents.base import AgentInterface, AgentResult
from crucible.agents.claude_code import ClaudeCodeAgent


def test_agent_result_dataclass():
    r = AgentResult(modified_files=[Path("train.py")], description="test change")
    assert r.description == "test change"
    assert r.modified_files == [Path("train.py")]


def test_claude_code_agent_generate_edit(tmp_path):
    """Test with mocked Claude Agent SDK query()."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    agent = ClaudeCodeAgent()

    # Simulate: agent edits the file and returns text + result messages
    async def mock_query(prompt, options=None):
        # Simulate the agent editing the file
        (tmp_path / "train.py").write_text("x = 2  # optimized")

        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        yield AssistantMessage(
            content=[TextBlock(text="Changed x to 2 for better performance")],
            model="claude-sonnet-4-20250514",
        )
        yield ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=800,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

    with patch("crucible.agents.claude_code.query", mock_query):
        result = agent.generate_edit("optimize x", tmp_path)

    assert Path("train.py") in result.modified_files
    assert len(result.description) > 0
    assert "Changed x" in result.description


def test_custom_system_prompt(tmp_path):
    from crucible.agents.claude_code import SYSTEM_PROMPT
    agent = ClaudeCodeAgent()
    assert agent.get_system_prompt(tmp_path) == SYSTEM_PROMPT
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir()
    (crucible_dir / "my_prompt.md").write_text("You are a custom agent.")
    agent = ClaudeCodeAgent(system_prompt_file="my_prompt.md")
    assert agent.get_system_prompt(tmp_path) == "You are a custom agent."


def test_claude_code_agent_error_handling(tmp_path):
    """Test that agent errors are handled gracefully."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    agent = ClaudeCodeAgent()

    async def mock_query_error(prompt, options=None):
        from claude_agent_sdk import ResultMessage
        yield ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=0,
            is_error=True,
            num_turns=0,
            session_id="test-session",
            result="API key invalid",
        )

    with patch("crucible.agents.claude_code.query", mock_query_error):
        result = agent.generate_edit("optimize x", tmp_path)

    assert result.modified_files == []
    assert "error" in result.description.lower()


def test_claude_code_agent_no_edits(tmp_path):
    """Test when agent responds but makes no file changes."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    agent = ClaudeCodeAgent()

    async def mock_query_noop(prompt, options=None):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        yield AssistantMessage(
            content=[TextBlock(text="No changes needed")],
            model="claude-sonnet-4-20250514",
        )
        yield ResultMessage(
            subtype="result",
            duration_ms=500,
            duration_api_ms=400,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

    with patch("crucible.agents.claude_code.query", mock_query_noop):
        result = agent.generate_edit("optimize x", tmp_path)

    assert result.modified_files == []
    assert "No changes needed" in result.description


# -- _clean_description tests -------------------------------------------------

from crucible.agents.claude_code import _clean_description


def test_clean_description_strips_markdown():
    assert _clean_description("**Change:** foo bar") == "foo bar"
    assert _clean_description("**Summary:** hello") == "hello"
    assert _clean_description("**bold text** rest") == "bold text rest"


def test_clean_description_preserves_plain():
    assert _clean_description("simple description") == "simple description"


def test_clean_description_truncates():
    long_text = "a" * 300
    result = _clean_description(long_text)
    assert len(result) == 200
