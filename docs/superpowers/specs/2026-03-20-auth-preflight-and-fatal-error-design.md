# Auth Preflight Check & Fatal Error Handling

**Date:** 2026-03-20
**Status:** Approved

## Problem

When Claude Code is not logged in, `crucible run` passes the existing preflight checks (which only verify CLI installation and `--version`), then enters the agent loop where every iteration silently fails. The agent returns empty `AgentResult` with `description="agent error: ..."`, which the orchestrator treats as a skip. This continues until `consecutive_skips` hits `max_retries`, wasting time with no actionable feedback.

## Solution

Two complementary changes:

1. **Preflight**: Add auth status check before starting an experiment
2. **Runtime**: Add structured error classification so the orchestrator can immediately abort on unrecoverable errors

## Design

### Part 1: Preflight — `check_claude_cli()` Auth Check

**File:** `src/crucible/preflight.py`

Add a third step to the existing `check_claude_cli()` function, after the `claude --version` check:

```python
import json

# 3. Check login status
try:
    auth = subprocess.run(
        ["claude", "auth", "status", "--json"],
        capture_output=True, text=True, timeout=10,
    )
except subprocess.TimeoutExpired:
    # Network may be slow — warn but don't block; let runtime handle it
    click.echo("Warning: claude auth status timed out, skipping auth check", err=True)
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
                "consider updating. Proceeding anyway.", err=True
            )
        else:
            raise click.ClickException(
                "Cannot determine claude auth status.\n"
                f"Output: {stderr}\n"
                "Try: claude login"
            )
```

**Edge cases:**
- `subprocess.TimeoutExpired` → warn but don't block (network may be slow; let runtime handle it)
- `JSONDecodeError` + exit code 0 → don't block (CLI version may differ in output format)
- `JSONDecodeError` + non-zero exit code + "unknown command" → warn about outdated CLI, don't block
- `JSONDecodeError` + non-zero exit code (other) → block with actionable message

### Part 2: `AgentErrorType` Enum & `AgentResult.error_type`

**File:** `src/crucible/agents/base.py`

```python
from enum import Enum

class AgentErrorType(Enum):
    AUTH = "auth"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"

@dataclass
class AgentResult:
    modified_files: list[Path]
    description: str
    usage: UsageInfo | None = None
    duration_seconds: float | None = None
    agent_output: str | None = None
    error_type: AgentErrorType | None = None  # None = success
```

`TIMEOUT` is informational/non-fatal — included for structured logging and future extensibility (e.g., `RATE_LIMIT` could be added later as another fatal type).

**File:** `src/crucible/agents/claude_code.py`

Error classification is encapsulated inside the agent:

```python
_AUTH_PATTERNS = {"not logged in", "unauthorized", "login required", "unauthenticated"}

def _classify_error(msg: str) -> AgentErrorType:
    lower = msg.lower()
    if any(p in lower for p in _AUTH_PATTERNS):
        return AgentErrorType.AUTH
    return AgentErrorType.UNKNOWN
```

Note: patterns are specific phrases to avoid false positives (e.g., "auth" alone matches "author"; "credential" matches "credential file in training data").

Applied in (order of precedence — inner handlers take priority):
- `_generate_edit_async()` `asyncio.TimeoutError` → `AgentErrorType.TIMEOUT` (inner)
- `_run_query()` `TimeoutError` → `AgentErrorType.TIMEOUT` (inner)
- `_run_query()` `ResultMessage.is_error` → `_classify_error(message.result)` (inner)
- `generate_edit()` outer `except Exception` → fallback `_classify_error(str(e))` (outer, catches anything not handled above)

The outer `except Exception` in `generate_edit()` is a fallback — inner handlers return `AgentResult` directly so exceptions from within `_generate_edit_async`/`_run_query` that are already handled won't reach the outer catch.

### Part 3: Orchestrator Fatal Error Handling

**File:** `src/crucible/orchestrator.py`

In `run_one_iteration()`, after the budget check (line ~168) and before hidden file stripping (line ~171):

```python
# Fatal error — unrecoverable, abort immediately
if agent_result.error_type == AgentErrorType.AUTH:
    logger.error(f"[iter {self._iteration}] Authentication error: {agent_result.description}")
    return "fatal"
```

Update `run_one_iteration()` docstring to include all return values: `"keep"`, `"discard"`, `"crash"`, `"violation"`, `"skip"`, `"budget_exceeded"`, `"fatal"`.

In `_run_loop_serial()`, before existing skip/failure checks:

```python
if status == "fatal":
    logger.error("Fatal error — cannot continue. Check: claude login")
    break
```

In `_run_loop_beam()`, after `run_one_iteration()` returns and before beam state sync-back — fatal breaks the entire outer `while True` loop:

```python
status = self.run_one_iteration()
session_count += 1

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

**Fatal behavior:**
- Does NOT increment `consecutive_failures` or `consecutive_skips`
- Does NOT write to results.tsv
- Does NOT perform git commit or revert
- Immediately breaks the run loop (entire loop, not just current beam)
- In beam mode, restores orchestrator state before breaking

## Files Changed

| File | Change |
|---|---|
| `src/crucible/agents/base.py` | Add `AgentErrorType` enum, add `error_type` field to `AgentResult` |
| `src/crucible/agents/claude_code.py` | Classify errors in `generate_edit` / `_run_query`, add `_classify_error()` |
| `src/crucible/preflight.py` | Add `claude auth status --json` check |
| `src/crucible/orchestrator.py` | Add fatal check in `run_one_iteration()`, immediate break in `_run_loop_serial()` and `_run_loop_beam()` |

## Backward Compatibility

- `error_type` defaults to `None` — existing agent implementations (including `FakeAgent` in tests) and all code constructing `AgentResult` are unaffected since the field has a default value and is added at the end
- Preflight change only affects `crucible run` command (already calls `check_claude_cli()`)
- New `"fatal"` return value from `run_one_iteration()` is only checked in the run loops; other callers (e.g., tests calling `run_one_iteration()` directly) will see it as a string but won't act on it unless they check

## Testing

- **`test_preflight.py`**: mock `subprocess.run` for auth status scenarios:
  - Logged in (happy path)
  - Not logged in (`loggedIn: false`)
  - JSON parse error + non-zero exit code
  - JSON parse error + "unknown command" in stderr (old CLI)
  - `subprocess.TimeoutExpired`
- **`test_agents.py`**: verify `error_type` is set correctly:
  - Auth error → `AgentErrorType.AUTH`
  - Timeout → `AgentErrorType.TIMEOUT`
  - Success → `None`
  - No false positives: benign messages containing "author", "authenticate model" etc. → `UNKNOWN`, not `AUTH`
- **`test_orchestrator.py` / `test_integration.py`**:
  - Fatal status immediately stops serial loop without incrementing counters or writing results
  - Fatal status breaks beam loop entirely (not just current beam), restoring orchestrator state
  - Resume after fatal exit works cleanly (no stale state)
