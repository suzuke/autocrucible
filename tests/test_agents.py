import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from crucible.agents.base import AgentErrorType, AgentInterface, AgentResult
from crucible.agents.claude_code import ClaudeCodeAgent, _classify_error


def test_agent_error_type_enum():
    assert AgentErrorType.AUTH.value == "auth"
    assert AgentErrorType.TIMEOUT.value == "timeout"
    assert AgentErrorType.UNKNOWN.value == "unknown"


def test_agent_result_error_type_default():
    r = AgentResult(modified_files=[], description="ok")
    assert r.error_type is None


def test_agent_result_error_type_set():
    r = AgentResult(
        modified_files=[], description="auth fail",
        error_type=AgentErrorType.AUTH,
    )
    assert r.error_type == AgentErrorType.AUTH


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


def test_language_appended_to_system_prompt(tmp_path):
    from crucible.agents.claude_code import SYSTEM_PROMPT
    # No language → default prompt unchanged
    agent = ClaudeCodeAgent()
    assert agent.get_system_prompt(tmp_path) == SYSTEM_PROMPT
    # With language → appended to default prompt
    agent = ClaudeCodeAgent(language="zh-TW")
    prompt = agent.get_system_prompt(tmp_path)
    assert prompt.startswith(SYSTEM_PROMPT)
    assert "zh-TW" in prompt
    # With language + custom prompt file
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir(exist_ok=True)
    (crucible_dir / "custom.md").write_text("Custom agent.")
    agent = ClaudeCodeAgent(system_prompt_file="custom.md", language="ja")
    prompt = agent.get_system_prompt(tmp_path)
    assert prompt.startswith("Custom agent.")
    assert "ja" in prompt


def test_inline_system_prompt(tmp_path):
    """system_prompt_file with multiline content is treated as inline prompt."""
    inline = "You are a custom agent.\nFocus on optimization."
    agent = ClaudeCodeAgent(system_prompt_file=inline)
    prompt = agent.get_system_prompt(tmp_path)
    assert prompt == inline


def test_missing_prompt_file_falls_back_to_default(tmp_path):
    """system_prompt_file with non-existent filename falls back to default SYSTEM_PROMPT."""
    from crucible.agents.claude_code import SYSTEM_PROMPT
    agent = ClaudeCodeAgent(system_prompt_file="nonexistent.md")
    prompt = agent.get_system_prompt(tmp_path)
    assert prompt == SYSTEM_PROMPT


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


@pytest.mark.parametrize("input_text,expected", [
    ("**Change:** foo bar", "foo bar"),
    ("**Summary:** hello", "hello"),
    ("**bold text** rest", "bold text rest"),
    ("simple description", "simple description"),
    ("a" * 300, "a" * 200),
])
def test_clean_description(input_text, expected):
    assert _clean_description(input_text) == expected


# -- hidden file hook tests ----------------------------------------------------

from crucible.agents.claude_code import _make_file_hooks, _resolve_rel_path


def test_resolve_rel_path_absolute():
    assert _resolve_rel_path("/project/secret.py", Path("/project")) == "secret.py"


def test_resolve_rel_path_relative():
    assert _resolve_rel_path("secret.py", Path("/project")) == "secret.py"


def test_resolve_rel_path_dotslash():
    assert _resolve_rel_path("./secret.py", Path("/project")) == "secret.py"


def test_resolve_rel_path_subdir():
    assert _resolve_rel_path("/project/lib/opponent.py", Path("/project")) == "lib/opponent.py"


def test_resolve_rel_path_outside_workspace():
    assert _resolve_rel_path("/other/file.py", Path("/project")) is None


def test_resolve_rel_path_empty():
    assert _resolve_rel_path("", Path("/project")) is None


@pytest.mark.asyncio
async def test_hook_denies_hidden_read():
    workspace = Path("/project")
    hooks = _make_file_hooks({"secret.py"}, {"train.py"}, workspace)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": "/project/secret.py"}},
        None, None,
    )
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "hidden" in output["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_hook_allows_read_non_hidden():
    workspace = Path("/project")
    hooks = _make_file_hooks({"secret.py"}, {"train.py"}, workspace)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": "train.py"}},
        None, None,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_hook_allows_read_non_editable():
    """Read tools can access non-editable, non-hidden files (e.g. readonly)."""
    workspace = Path("/project")
    hooks = _make_file_hooks(set(), {"train.py"}, workspace)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": "results.jsonl"}},
        None, None,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_hook_denies_write_non_editable():
    """Write tools are denied for files not in editable list."""
    workspace = Path("/project")
    hooks = _make_file_hooks(set(), {"train.py"}, workspace)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Edit", "tool_input": {"file_path": "results.jsonl"}},
        None, None,
    )
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "not in the editable" in output["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_hook_allows_write_editable():
    """Write tools are allowed for editable files."""
    workspace = Path("/project")
    hooks = _make_file_hooks(set(), {"train.py"}, workspace)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Edit", "tool_input": {"file_path": "train.py"}},
        None, None,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_hook_allows_no_path():
    workspace = Path("/project")
    hooks = _make_file_hooks({"secret.py"}, {"train.py"}, workspace)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Grep", "tool_input": {"pattern": "SECRET"}},
        None, None,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_hook_denies_hidden_subdir():
    workspace = Path("/project")
    hooks = _make_file_hooks({"lib/opponent.py"}, {"train.py"}, workspace)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": "/project/lib/opponent.py"}},
        None, None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_blocks_env_read(tmp_path):
    """Read tool on .env should be denied."""
    hooks = _make_file_hooks(set(), set(), tmp_path)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / ".env")}},
        None, None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "sensitive" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


@pytest.mark.asyncio
async def test_hook_blocks_ssh_dir_read(tmp_path):
    """Read tool on .ssh/config should be denied."""
    hooks = _make_file_hooks(set(), set(), tmp_path)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / ".ssh" / "config")}},
        None, None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_allows_normal_read(tmp_path):
    """Read tool on a regular file should not be denied by sensitive pattern check."""
    hooks = _make_file_hooks(set(), set(), tmp_path)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "solution.py")}},
        None, None,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_hook_blocks_env_local_read(tmp_path):
    """.env.local should be blocked (prefix + dot extension)."""
    hooks = _make_file_hooks(set(), set(), tmp_path)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / ".env.local")}},
        None, None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_allows_envrc_read(tmp_path):
    """.envrc should NOT be blocked (not a dot-prefixed extension of .env)."""
    hooks = _make_file_hooks(set(), set(), tmp_path)
    hook_fn = hooks["PreToolUse"][0].hooks[0]
    result = await hook_fn(
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / ".envrc")}},
        None, None,
    )
    assert result == {}


# -- _classify_error tests ----------------------------------------------------


def test_classify_error_auth_patterns():
    assert _classify_error("not logged in") == AgentErrorType.AUTH
    assert _classify_error("Error: unauthorized") == AgentErrorType.AUTH
    assert _classify_error("login required to proceed") == AgentErrorType.AUTH
    assert _classify_error("request unauthenticated") == AgentErrorType.AUTH


def test_classify_error_no_false_positives():
    """Benign messages should not trigger AUTH classification."""
    assert _classify_error("updated the author field") == AgentErrorType.UNKNOWN
    assert _classify_error("authenticate model parameters") == AgentErrorType.UNKNOWN
    assert _classify_error("credential file in training data") == AgentErrorType.UNKNOWN
    assert _classify_error("some random error") == AgentErrorType.UNKNOWN


# -- ClaudeCodeAgent error_type integration tests ------------------------------


def test_claude_code_agent_auth_error_type(tmp_path):
    """Agent sets error_type=AUTH when SDK returns auth error."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    agent = ClaudeCodeAgent()
    async def mock_query_auth_error(prompt, options=None):
        from claude_agent_sdk import ResultMessage
        yield ResultMessage(
            subtype="result", duration_ms=100, duration_api_ms=0,
            is_error=True, num_turns=0, session_id="test-session",
            result="not logged in",
        )
    with patch("crucible.agents.claude_code.query", mock_query_auth_error):
        result = agent.generate_edit("optimize x", tmp_path)
    assert result.error_type == AgentErrorType.AUTH


def test_claude_code_agent_timeout_error_type(tmp_path):
    """Agent sets error_type=TIMEOUT on timeout."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    agent = ClaudeCodeAgent(timeout=1)
    async def mock_query_slow(prompt, options=None):
        import asyncio
        await asyncio.sleep(10)
        yield  # never reached
    with patch("crucible.agents.claude_code.query", mock_query_slow):
        result = agent.generate_edit("optimize x", tmp_path)
    assert result.error_type == AgentErrorType.TIMEOUT


def test_claude_code_agent_success_no_error_type(tmp_path):
    """Successful agent run has error_type=None."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    agent = ClaudeCodeAgent()
    async def mock_query_ok(prompt, options=None):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        yield AssistantMessage(content=[TextBlock(text="Done")], model="claude-sonnet-4-20250514")
        yield ResultMessage(subtype="result", duration_ms=100, duration_api_ms=80, is_error=False, num_turns=1, session_id="test")
    with patch("crucible.agents.claude_code.query", mock_query_ok):
        result = agent.generate_edit("optimize x", tmp_path)
    assert result.error_type is None


# -- capabilities tests --------------------------------------------------------


def test_capabilities_default():
    """Default capabilities returns all five tools."""
    agent = ClaudeCodeAgent()
    caps = agent.capabilities()
    assert caps == {"read", "edit", "write", "glob", "grep"}


# -- agent factory tests -------------------------------------------------------

from crucible.agents import create_agent
from crucible.config import AgentConfig


def test_create_agent_claude_code():
    """Factory creates ClaudeCodeAgent for claude-code type."""
    config = AgentConfig(type="claude-code")
    agent = create_agent(config, timeout=120)
    assert isinstance(agent, ClaudeCodeAgent)
    assert agent.timeout == 120


def test_create_agent_unknown_raises():
    """Factory raises ValueError for unknown agent type."""
    config = AgentConfig(type="nonexistent")
    with pytest.raises(ValueError, match="Unknown agent type: nonexistent"):
        create_agent(config)


def test_create_agent_claude_code_with_kwargs():
    """Factory passes kwargs through to ClaudeCodeAgent."""
    config = AgentConfig(type="claude-code", system_prompt="custom.md")
    agent = create_agent(
        config,
        timeout=300,
        model="opus",
        system_prompt_file="custom.md",
        hidden_files={"secret.py"},
    )
    assert isinstance(agent, ClaudeCodeAgent)
    assert agent.timeout == 300
    assert agent.model == "opus"
    assert agent.system_prompt_file == "custom.md"
    assert agent.hidden_files == {"secret.py"}


# -- timing tests --------------------------------------------------------------


def test_run_query_includes_duration(tmp_path):
    """_run_query sets duration_seconds on the result."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    agent = ClaudeCodeAgent()

    async def mock_query(prompt, options=None):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        yield AssistantMessage(
            content=[TextBlock(text="Added timing")],
            model="claude-sonnet-4-20250514",
        )
        yield ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

    with patch("crucible.agents.claude_code.query", mock_query):
        result = agent.generate_edit("optimize x", tmp_path)

    assert result.duration_seconds is not None
    assert result.duration_seconds >= 0


# -- AgentConfig model/base_url tests -----------------------------------------


def test_agent_config_model_base_url():
    """AgentConfig supports model and base_url fields."""
    config = AgentConfig(type="ollama", model="llama3", base_url="http://localhost:11434")
    assert config.model == "llama3"
    assert config.base_url == "http://localhost:11434"


