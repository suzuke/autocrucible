# Sensitive File Protection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Block agent Read access to credential files (.env, .ssh/, etc.) via PreToolUse hook, and add Docker shadow mounts as defense-in-depth.

**Architecture:** Extend existing `_make_file_hooks()` in `claude_code.py` to check Read tool inputs against hardcoded sensitive patterns (directory names + file prefixes). Add shadow mounts for .env variants in `SandboxRunner._docker_run()`.

**Tech Stack:** Python, claude_agent_sdk (HookMatcher), pytest (async tests)

---

### Task 1: Add sensitive pattern matching to the Read hook

**Files:**
- Modify: `src/crucible/agents/claude_code.py`
- Test: `tests/test_agents.py`

**Step 1: Write 3 failing tests**

Add to `tests/test_agents.py` (after existing hook tests):

```python
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
    # Should return empty dict (no deny) — hidden/editable checks don't apply here
    assert result == {}
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/suzuke/Documents/Hack/crucible
uv run pytest tests/test_agents.py::test_hook_blocks_env_read tests/test_agents.py::test_hook_blocks_ssh_dir_read tests/test_agents.py::test_hook_allows_normal_read -v
```

Expected: FAIL (no sensitive file blocking yet)

**Step 3: Add pattern constants and matching function to `claude_code.py`**

Add after the existing imports/constants, before `_resolve_rel_path`:

```python
# Sensitive file patterns — hardcoded, not configurable (prevents agent self-escalation)
_SENSITIVE_DIR_PATTERNS: frozenset[str] = frozenset({
    ".ssh", ".aws", ".gnupg", ".kube", ".azure", ".gcloud",
})

_SENSITIVE_FILE_PREFIXES: frozenset[str] = frozenset({
    ".env",
})


def _is_sensitive_path(rel: str) -> bool:
    """Return True if the relative path matches any sensitive pattern.

    Checks:
    - Any path component matches a sensitive directory name exactly
    - The filename starts with a sensitive file prefix
    """
    parts = Path(rel).parts
    for part in parts:
        if part in _SENSITIVE_DIR_PATTERNS:
            return True
    filename = parts[-1] if parts else ""
    for prefix in _SENSITIVE_FILE_PREFIXES:
        if filename == prefix or filename.startswith(prefix + "."):
            return True
    return False
```

**Step 4: Add sensitive file check inside `pre_tool_use_hook`**

In `_make_file_hooks()`, inside `pre_tool_use_hook`, add this block **after** the hidden files check and **before** the write-tools check:

```python
        # Deny read access to sensitive credential files
        _read_tools = {"Read", "Glob", "Grep"}
        if tool_name in _read_tools and _is_sensitive_path(rel):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Access denied: {rel} matches sensitive file pattern. "
                        "Crucible does not allow reading credential or key files."
                    ),
                }
            }
```

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_agents.py::test_hook_blocks_env_read tests/test_agents.py::test_hook_blocks_ssh_dir_read tests/test_agents.py::test_hook_allows_normal_read -v
```

Expected: PASS (all 3)

**Step 6: Run full test suite to check for regressions**

```bash
uv run pytest tests/test_agents.py -v
```

Expected: all existing tests still pass

**Step 7: Commit**

```bash
git add src/crucible/agents/claude_code.py tests/test_agents.py
git commit -m "feat: block agent Read access to sensitive credential files via PreToolUse hook"
```

---

### Task 2: Add Docker shadow mounts for .env files

**Files:**
- Modify: `src/crucible/sandbox.py`

**Step 1: Write a failing test**

Add to `tests/test_sandbox.py` (or create if missing):

```python
def test_docker_shadows_env_file(tmp_path):
    """Docker run args should include shadow mount for .env if it exists."""
    from crucible.config import SandboxConfig
    from crucible.sandbox import SandboxRunner

    # Create a .env file in workspace
    (tmp_path / ".env").write_text("SECRET=abc123")

    config = SandboxConfig(backend="docker", base_image="python:3.11-slim")
    runner = SandboxRunner(config=config, workspace=tmp_path)

    # Patch subprocess.Popen to capture args
    import unittest.mock as mock
    with mock.patch("crucible.sandbox.subprocess.Popen") as mock_popen:
        mock_proc = mock.MagicMock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        runner._docker_run("echo test", 30)

        args = mock_popen.call_args[0][0]
        args_str = " ".join(args)
        assert "/dev/null:/workspace/.env:ro" in args_str
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_sandbox.py::test_docker_shadows_env_file -v
```

Expected: FAIL

**Step 3: Add shadow mounts in `_docker_run()`**

In `sandbox.py`, inside `_docker_run()`, add after the workspace `:ro` mount line and before the editable files mounts:

```python
        # Shadow .env variants with /dev/null so agent-generated code cannot
        # read secrets even if the hook is bypassed (defense-in-depth)
        for env_name in (".env", ".env.local", ".env.production", ".env.staging"):
            if (self.workspace / env_name).exists():
                cmd.extend(["-v", f"/dev/null:/workspace/{env_name}:ro"])
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_sandbox.py::test_docker_shadows_env_file -v
```

Expected: PASS

**Step 5: Run full test suite**

```bash
uv run pytest -v
```

Expected: all tests pass

**Step 6: Commit**

```bash
git add src/crucible/sandbox.py tests/test_sandbox.py
git commit -m "feat: shadow .env files with /dev/null in Docker sandbox for defense-in-depth"
```
