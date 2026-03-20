# Auth Preflight & Fatal Error Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect auth failures early (preflight) and immediately (runtime) instead of silently wasting iterations.

**Architecture:** Add `AgentErrorType` enum to classify agent errors structurally. Preflight runs `claude auth status --json` before starting. Orchestrator checks `error_type` on `AgentResult` and returns `"fatal"` for unrecoverable errors, breaking the run loop immediately.

**Tech Stack:** Python 3.10+, Click CLI, Claude Agent SDK, pytest

**Spec:** `docs/superpowers/specs/2026-03-20-auth-preflight-and-fatal-error-design.md`

---

### Task 1: Add `AgentErrorType` enum and `error_type` field to `AgentResult`

**Files:**
- Modify: `src/crucible/agents/base.py:1-26`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write test for AgentErrorType and error_type field**

```python
# Add to tests/test_agents.py
from crucible.agents.base import AgentErrorType

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agents.py::test_agent_error_type_enum tests/test_agents.py::test_agent_result_error_type_default tests/test_agents.py::test_agent_result_error_type_set -v`
Expected: FAIL (ImportError — `AgentErrorType` doesn't exist yet)

- [ ] **Step 3: Add enum and field to base.py**

In `src/crucible/agents/base.py`, add the enum before `AgentResult` and the field at the end of the dataclass:

```python
from enum import Enum

class AgentErrorType(Enum):
    AUTH = "auth"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
```

Add to `AgentResult` dataclass after `agent_output`:
```python
    error_type: AgentErrorType | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agents.py::test_agent_error_type_enum tests/test_agents.py::test_agent_result_error_type_default tests/test_agents.py::test_agent_result_error_type_set -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run pytest -v`
Expected: All existing tests still pass (field has default `None`)

- [ ] **Step 6: Commit**

```bash
git add src/crucible/agents/base.py tests/test_agents.py
git commit -m "feat: add AgentErrorType enum and error_type field to AgentResult"
```

---

### Task 2: Add `_classify_error()` and wire error types into `ClaudeCodeAgent`

**Files:**
- Modify: `src/crucible/agents/claude_code.py:195-272`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write tests for _classify_error**

```python
# Add to tests/test_agents.py
from crucible.agents.claude_code import _classify_error

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agents.py::test_classify_error_auth_patterns tests/test_agents.py::test_classify_error_no_false_positives -v`
Expected: FAIL (ImportError — `_classify_error` doesn't exist)

- [ ] **Step 3: Add _classify_error function**

Add to `src/crucible/agents/claude_code.py`, after the `from crucible.agents.base import ...` import line (line 23). Update import to include `AgentErrorType`:

```python
from crucible.agents.base import AgentErrorType, AgentInterface, AgentResult
```

Add before the `ClaudeCodeAgent` class (after `DEFAULT_AGENT_TIMEOUT`):

```python
_AUTH_PATTERNS = {"not logged in", "unauthorized", "login required", "unauthenticated"}

def _classify_error(msg: str) -> AgentErrorType:
    """Classify an error message into an AgentErrorType."""
    lower = msg.lower()
    if any(p in lower for p in _AUTH_PATTERNS):
        return AgentErrorType.AUTH
    return AgentErrorType.UNKNOWN
```

- [ ] **Step 4: Run classify tests to verify they pass**

Run: `uv run pytest tests/test_agents.py::test_classify_error_auth_patterns tests/test_agents.py::test_classify_error_no_false_positives -v`
Expected: PASS

- [ ] **Step 5: Write test for error_type on agent auth error**

```python
# Add to tests/test_agents.py
def test_claude_code_agent_auth_error_type(tmp_path):
    """Agent sets error_type=AUTH when SDK returns auth error."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    agent = ClaudeCodeAgent()

    async def mock_query_auth_error(prompt, options=None):
        from claude_agent_sdk import ResultMessage
        yield ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=0,
            is_error=True,
            num_turns=0,
            session_id="test-session",
            result="not logged in",
        )

    with patch("crucible.agents.claude_code.query", mock_query_auth_error):
        result = agent.generate_edit("optimize x", tmp_path)

    assert result.error_type == AgentErrorType.AUTH
```

- [ ] **Step 6: Write test for error_type on timeout**

```python
# Add to tests/test_agents.py
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
```

- [ ] **Step 7: Write test for error_type None on success**

```python
# Add to tests/test_agents.py
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
        yield AssistantMessage(
            content=[TextBlock(text="Done")],
            model="claude-sonnet-4-20250514",
        )
        yield ResultMessage(
            subtype="result", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="test",
        )

    with patch("crucible.agents.claude_code.query", mock_query_ok):
        result = agent.generate_edit("optimize x", tmp_path)

    assert result.error_type is None
```

- [ ] **Step 8: Run new tests to verify they fail**

Run: `uv run pytest tests/test_agents.py::test_claude_code_agent_auth_error_type tests/test_agents.py::test_claude_code_agent_timeout_error_type tests/test_agents.py::test_claude_code_agent_success_no_error_type -v`
Expected: FAIL (error_type not set in agent code yet)

- [ ] **Step 9: Wire error_type into ClaudeCodeAgent**

In `src/crucible/agents/claude_code.py`, modify three locations:

**a) `generate_edit()` outer except (line 204-205):** Change:
```python
        except Exception as e:
            return AgentResult(modified_files=[], description=f"agent error: {e}")
```
To:
```python
        except Exception as e:
            return AgentResult(
                modified_files=[], description=f"agent error: {e}",
                error_type=_classify_error(str(e)),
            )
```

**b) `_generate_edit_async()` timeout (line 217-218):** Change:
```python
        except asyncio.TimeoutError:
            return AgentResult(modified_files=[], description="claude agent timed out")
```
To:
```python
        except asyncio.TimeoutError:
            return AgentResult(
                modified_files=[], description="claude agent timed out",
                error_type=AgentErrorType.TIMEOUT,
            )
```

**c) `_run_query()` ResultMessage.is_error (line 259-264):** Change:
```python
                        return AgentResult(
                            modified_files=[],
                            description=f"agent error: {message.result or 'unknown'}",
                            duration_seconds=duration,
                            agent_output=agent_output,
                        )
```
To:
```python
                        error_msg = message.result or "unknown"
                        return AgentResult(
                            modified_files=[],
                            description=f"agent error: {error_msg}",
                            duration_seconds=duration,
                            agent_output=agent_output,
                            error_type=_classify_error(error_msg),
                        )
```

**d) `_run_query()` TimeoutError catch (line 265-272):** Change:
```python
        except TimeoutError:
            duration = time.monotonic() - start
            agent_output = "\n".join(all_text_parts) if all_text_parts else None
            return AgentResult(
                modified_files=[], description="claude agent timed out",
                duration_seconds=duration,
                agent_output=agent_output,
            )
```
To:
```python
        except TimeoutError:
            duration = time.monotonic() - start
            agent_output = "\n".join(all_text_parts) if all_text_parts else None
            return AgentResult(
                modified_files=[], description="claude agent timed out",
                duration_seconds=duration,
                agent_output=agent_output,
                error_type=AgentErrorType.TIMEOUT,
            )
```

- [ ] **Step 10: Run new tests to verify they pass**

Run: `uv run pytest tests/test_agents.py::test_claude_code_agent_auth_error_type tests/test_agents.py::test_claude_code_agent_timeout_error_type tests/test_agents.py::test_claude_code_agent_success_no_error_type -v`
Expected: PASS

- [ ] **Step 11: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

- [ ] **Step 12: Commit**

```bash
git add src/crucible/agents/claude_code.py tests/test_agents.py
git commit -m "feat: classify agent errors with AgentErrorType in ClaudeCodeAgent"
```

---

### Task 3: Add auth check to preflight

**Files:**
- Modify: `src/crucible/preflight.py:1-35`
- Test: `tests/test_preflight.py`

Note: existing tests mock `subprocess.run` as a single mock. After this change, `check_claude_cli()` calls `subprocess.run` twice (version + auth). Tests need `side_effect` to return different results for each call.

**Prerequisite:** Update imports at top of `tests/test_preflight.py`:
```python
# Change: from unittest.mock import patch
# To:
from unittest.mock import patch, MagicMock
import subprocess as _subprocess
```

- [ ] **Step 1: Write test for auth check — logged in (happy path)**

```python
# Add to tests/test_preflight.py

def test_check_claude_cli_auth_logged_in():
    """No exception when auth status shows logged in."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(
        returncode=0,
        stdout='{"loggedIn": true}',
        stderr="",
    )
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        check_claude_cli()  # should not raise
```

Add import at top of test file:
```python
from unittest.mock import patch, MagicMock
```

- [ ] **Step 2: Write test for auth check — not logged in**

```python
def test_check_claude_cli_auth_not_logged_in():
    """Raise when auth status shows not logged in."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(
        returncode=0,
        stdout='{"loggedIn": false}',
        stderr="",
    )
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        with pytest.raises(click.ClickException, match="not logged in"):
            check_claude_cli()
```

- [ ] **Step 3: Write test for auth check — JSON parse error + non-zero exit**

```python
def test_check_claude_cli_auth_json_error():
    """Raise when auth output is not valid JSON and exit code non-zero."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(
        returncode=1,
        stdout="not json",
        stderr="some error",
    )
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        with pytest.raises(click.ClickException, match="Cannot determine"):
            check_claude_cli()
```

- [ ] **Step 4: Write test for auth check — old CLI ("unknown command")**

```python
def test_check_claude_cli_auth_old_cli(capsys):
    """Warn but don't raise when CLI doesn't support auth status."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(
        returncode=1,
        stdout="",
        stderr="error: unknown command 'auth'",
    )
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        check_claude_cli()  # should not raise
    captured = capsys.readouterr()
    assert "too old" in captured.err
```

- [ ] **Step 5: Write test for auth check — subprocess timeout**

```python
def test_check_claude_cli_auth_timeout(capsys):
    """Warn but don't raise when auth status times out."""
    version_result = MagicMock(returncode=0)
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[
            version_result,
            _subprocess.TimeoutExpired(cmd="claude", timeout=10),
        ]),
    ):
        check_claude_cli()  # should not raise
    captured = capsys.readouterr()
    assert "timed out" in captured.err
```

- [ ] **Step 6: Fix existing tests for two subprocess.run calls**

The existing `test_check_claude_cli_ok` test uses a single mock that returns `returncode=0`. Now `check_claude_cli()` calls `subprocess.run` twice. Update it:

```python
def test_check_claude_cli_ok():
    """No exception when claude CLI works and is logged in."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(
        returncode=0,
        stdout='{"loggedIn": true}',
        stderr="",
    )
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        check_claude_cli()  # should not raise
```

The existing `test_check_claude_cli_not_found` test is fine (exits before `subprocess.run`). The `test_check_claude_cli_broken` test is fine (first `subprocess.run` returns non-zero, exits before auth call).

- [ ] **Step 7: Run new tests to verify they fail**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: New tests FAIL (auth check not implemented), `test_check_claude_cli_ok` also FAILS (now has side_effect but function only calls subprocess.run once)

- [ ] **Step 8: Implement auth check in preflight.py**

Replace `src/crucible/preflight.py` content:

```python
"""Preflight checks — fail fast before starting an experiment."""

from __future__ import annotations

import json
import shutil
import subprocess

import click


def check_claude_cli() -> None:
    """Verify that the claude CLI is installed, responsive, and logged in.

    Raises click.ClickException with actionable guidance on failure.
    """
    if not shutil.which("claude"):
        raise click.ClickException(
            "claude CLI not found on PATH.\n"
            "Install: npm install -g @anthropic-ai/claude-code\n"
            "Then authenticate: claude login"
        )

    result = subprocess.run(
        ["claude", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise click.ClickException(
            "claude CLI found but not working.\n"
            f"Error: {result.stderr.strip()}\n"
            "Try: claude login"
        )

    # Check login status
    try:
        auth = subprocess.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        click.echo(
            "Warning: claude auth status timed out, skipping auth check",
            err=True,
        )
        return

    try:
        data = json.loads(auth.stdout)
        if not data.get("loggedIn"):
            raise click.ClickException(
                "claude CLI is not logged in.\n"
                "Run: claude login"
            )
    except json.JSONDecodeError:
        if auth.returncode != 0:
            stderr = auth.stderr.strip() or auth.stdout.strip()
            if "unknown command" in stderr.lower():
                click.echo(
                    "Warning: claude CLI too old to check auth status; "
                    "consider updating. Proceeding anyway.",
                    err=True,
                )
            else:
                raise click.ClickException(
                    "Cannot determine claude auth status.\n"
                    f"Output: {stderr}\n"
                    "Try: claude login"
                )
```

- [ ] **Step 9: Run all preflight tests**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: All PASS

- [ ] **Step 10: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

- [ ] **Step 11: Commit**

```bash
git add src/crucible/preflight.py tests/test_preflight.py
git commit -m "feat: add auth login check to preflight"
```

---

### Task 4: Add fatal error handling to orchestrator

**Files:**
- Modify: `src/crucible/orchestrator.py:141-144, 394-540`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write test for fatal status in run_one_iteration**

```python
# Add to tests/test_orchestrator.py
from crucible.agents.base import AgentErrorType

def test_fatal_error_stops_immediately(tmp_path):
    """run_one_iteration returns 'fatal' on auth error, no results written."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()
    mock_agent.capabilities.return_value = {"read", "edit", "write", "glob", "grep"}

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    mock_agent.generate_edit.return_value = AgentResult(
        modified_files=[], description="agent error: not logged in",
        error_type=AgentErrorType.AUTH,
    )

    result = orch.run_one_iteration()
    assert result == "fatal"
    # No results written
    assert len(orch.results.read_all()) == 0
    # Counters not incremented
    assert orch._consecutive_failures == 0
    assert orch._consecutive_skips == 0
```

- [ ] **Step 2: Write test for fatal breaking serial loop**

```python
def test_fatal_breaks_serial_loop(tmp_path):
    """Serial loop stops after first fatal, runs only 1 iteration."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()
    mock_agent.capabilities.return_value = {"read", "edit", "write", "glob", "grep"}

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    mock_agent.generate_edit.return_value = AgentResult(
        modified_files=[], description="agent error: not logged in",
        error_type=AgentErrorType.AUTH,
    )

    orch._run_loop_serial(max_iterations=5)
    # Agent called only once — fatal stopped the loop
    assert mock_agent.generate_edit.call_count == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py::test_fatal_error_stops_immediately tests/test_orchestrator.py::test_fatal_breaks_serial_loop -v`
Expected: FAIL (`"fatal"` not returned — no check for `error_type` in orchestrator yet)

- [ ] **Step 4: Add fatal check to run_one_iteration**

In `src/crucible/orchestrator.py`, add import at top:
```python
from crucible.agents.base import AgentErrorType
```

Update `run_one_iteration()` docstring (line 142-144):
```python
    def run_one_iteration(self) -> str:
        """Execute one full experiment cycle.

        Returns a status string: "keep", "discard", "crash", "violation",
        "skip", "budget_exceeded", or "fatal".
        """
```

After the budget check block (after line 168, before `# 3. Strip hidden files`), add:
```python
        # Fatal error — unrecoverable, abort immediately
        if agent_result.error_type == AgentErrorType.AUTH:
            logger.error(
                f"[iter {self._iteration}] Authentication error: "
                f"{agent_result.description}"
            )
            return "fatal"
```

- [ ] **Step 5: Add fatal check to _run_loop_serial**

In `_run_loop_serial()`, after `session_count += 1` (line 411) and before the `best = ...` line (line 413), add:

```python
                if status == "fatal":
                    logger.error("Fatal error — cannot continue. Check: claude login")
                    break
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py::test_fatal_error_stops_immediately tests/test_orchestrator.py::test_fatal_breaks_serial_loop -v`
Expected: PASS

- [ ] **Step 7: Write test for fatal breaking beam loop**

```python
from crucible.config import SearchConfig

def test_fatal_breaks_beam_loop(tmp_path):
    """Fatal in beam mode stops the entire loop, not just the current beam."""
    setup_repo(tmp_path)
    cfg = make_config()
    # Enable beam search
    cfg.search = SearchConfig(strategy="beam", beam_width=2)
    mock_agent = MagicMock()
    mock_agent.capabilities.return_value = {"read", "edit", "write", "glob", "grep"}

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()
    orch.init_beams()

    mock_agent.generate_edit.return_value = AgentResult(
        modified_files=[], description="agent error: not logged in",
        error_type=AgentErrorType.AUTH,
    )

    orch._run_loop_beam(max_iterations=5)
    # Agent called only once — fatal stopped the entire beam loop
    assert mock_agent.generate_edit.call_count == 1
    # Orchestrator state restored (not stuck on beam)
    assert orch._current_beam_id is None
```

- [ ] **Step 8: Add fatal check to _run_loop_beam**

In `_run_loop_beam()`, after `session_count += 1` (line 515) and before `# Sync beam state back` (line 517), add:

```python
                if status == "fatal":
                    logger.error("Fatal error — cannot continue. Check: claude login")
                    # Restore orchestrator state before breaking
                    self.results = orig_results
                    self.context = orig_context
                    self._fail_seq = orig_fail_seq
                    self._consecutive_failures = orig_consec_fail
                    self._consecutive_skips = orig_consec_skip
                    self._iteration = orig_iter
                    self._current_beam_id = None
                    break
```

- [ ] **Step 9: Write test for resume after fatal**

```python
def test_resume_after_fatal_works(tmp_path):
    """After fatal exit, resume() + re-run works without stale state."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()
    mock_agent.capabilities.return_value = {"read", "edit", "write", "glob", "grep"}

    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    # First run: fatal error
    mock_agent.generate_edit.return_value = AgentResult(
        modified_files=[], description="agent error: not logged in",
        error_type=AgentErrorType.AUTH,
    )
    orch._run_loop_serial(max_iterations=5)
    assert mock_agent.generate_edit.call_count == 1

    # Resume: should pick up cleanly
    orch.resume()
    assert orch._consecutive_failures == 0
    assert orch._consecutive_skips == 0
```

- [ ] **Step 10: Run all orchestrator tests**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS (after fatal check is implemented in steps 4-8)

- [ ] **Step 11: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

- [ ] **Step 12: Commit**

```bash
git add src/crucible/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator aborts immediately on fatal agent errors"
```
