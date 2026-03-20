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
auth = subprocess.run(
    ["claude", "auth", "status", "--json"],
    capture_output=True, text=True, timeout=10,
)
try:
    data = json.loads(auth.stdout)
    if not data.get("loggedIn"):
        raise click.ClickException(
            "claude CLI is not logged in.\n"
            "Run: claude login"
        )
except json.JSONDecodeError:
    if auth.returncode != 0:
        raise click.ClickException(
            "Cannot determine claude auth status.\n"
            f"Output: {auth.stderr.strip() or auth.stdout.strip()}\n"
            "Try: claude login"
        )
```

**Edge cases:**
- `subprocess.TimeoutExpired` → warn but don't block (network may be slow; let runtime handle it)
- `JSONDecodeError` + exit code 0 → don't block (CLI version may differ in output format)
- `JSONDecodeError` + non-zero exit code → block with actionable message

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

**File:** `src/crucible/agents/claude_code.py`

Error classification is encapsulated inside the agent:

```python
_AUTH_PATTERNS = {"not logged in", "unauthorized", "auth", "login required", "credential"}

def _classify_error(msg: str) -> AgentErrorType:
    lower = msg.lower()
    if any(p in lower for p in _AUTH_PATTERNS):
        return AgentErrorType.AUTH
    return AgentErrorType.UNKNOWN
```

Applied in:
- `generate_edit()` outer `except Exception` → `_classify_error(str(e))`
- `_run_query()` `ResultMessage.is_error` → `_classify_error(message.result)`
- `_generate_edit_async()` `asyncio.TimeoutError` → `AgentErrorType.TIMEOUT`

### Part 3: Orchestrator Fatal Error Handling

**File:** `src/crucible/orchestrator.py`

In `run_one_iteration()`, after calling `agent.generate_edit()` and before guardrails:

```python
if agent_result.error_type == AgentErrorType.AUTH:
    logger.error(f"[iter {self._iteration}] Authentication error: {agent_result.description}")
    return "fatal"
```

In `run()` loop, before existing skip/failure checks:

```python
if status == "fatal":
    logger.error("Fatal error — cannot continue. Check: claude login")
    break
```

In `run_beam()`, same treatment — fatal breaks the entire loop, not just the current beam.

**Fatal behavior:**
- Does NOT increment `consecutive_failures` or `consecutive_skips`
- Does NOT write to results.tsv
- Does NOT perform git commit or revert
- Immediately breaks the run loop

## Files Changed

| File | Change |
|---|---|
| `src/crucible/agents/base.py` | Add `AgentErrorType` enum, add `error_type` field to `AgentResult` |
| `src/crucible/agents/claude_code.py` | Classify errors in `generate_edit` / `_run_query`, add `_classify_error()` |
| `src/crucible/preflight.py` | Add `claude auth status --json` check |
| `src/crucible/orchestrator.py` | Add fatal check in `run_one_iteration()`, immediate break in `run()` and `run_beam()` |

## Backward Compatibility

- `error_type` defaults to `None` — existing agent implementations and tests are unaffected
- Preflight change only affects `crucible run` command (already calls `check_claude_cli()`)
- New `"fatal"` return value from `run_one_iteration()` is only checked in the run loops

## Testing

- `test_preflight.py`: mock `subprocess.run` for auth status (logged in, not logged in, JSON parse error, timeout)
- `test_agents.py`: verify `error_type` is set correctly for auth errors, timeouts, and success cases
- `test_orchestrator.py` / `test_integration.py`: verify fatal status immediately stops the loop without incrementing counters or writing results
