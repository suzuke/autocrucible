# Feature Completeness Design

Date: 2026-03-09

## Goal

Supplement crucible with 6 missing features to make it production-ready.

## Features

### 1. Resume (auto-continue on existing branch)

`crucible run --tag run1` detects existing branch `crucible/run1`:
- `git checkout` to the branch (instead of failing)
- Read existing `results.tsv` to restore best metric and iteration count
- Log INFO: "Resuming experiment on crucible/run1 (N previous iterations)"
- `init` behavior unchanged — only creates new branches

Agent continuity: not needed. Each iteration is already a stateless agent call. Context is reconstructed from `results.tsv` via `ContextAssembler`. Resume produces identical prompts to uninterrupted runs.

**Modified:** `cli.py` (run command), `orchestrator.py` (init logic)

### 2. Structured Logging

Replace all `print()` with `logging` module:
- CLI adds `--verbose` / `-v` global flag → logging level DEBUG
- Default level: INFO (same visible output as current print)
- Format: `[HH:MM:SS] LEVEL message`
- No file logging (stdout only)

**Modified:** `cli.py` (flag + logging config), all modules (print → logging)

### 3. Validate Command

New `crucible validate` command checks project health:

1. `.crucible/config.yaml` syntax + required fields
2. `program.md` exists and non-empty
3. All editable/readonly files exist
4. `commands.run` executes successfully (30s timeout)
5. `commands.eval` output parses to a valid metric

Each check shows PASS/FAIL with reason.

**New file:** `validator.py`
**Modified:** `cli.py`

### 4. Experiment Comparison

New `crucible compare tag1 tag2` command:
- Reads each tag's `results.tsv` via `git show crucible/<tag>:results.tsv`
- Outputs comparison table:

```
              run1        run2
Iterations    15          22
Kept          8           14
Best metric   142000.0    158000.0
Best commit   b2c3d4e     f7a8b9c
```

- Supports `--json` flag

**Modified:** `cli.py`, `results.py` (add `read_from_git_ref` method)

### 5. JSON Output

Add `--json` flag to `status`, `history`, and `compare`:
- `status --json` → `{"total": N, "kept": N, "discarded": N, "crashed": N, "best": {...}}`
- `history --json` → `[{"commit": "...", "metric": 0.5, "status": "keep", "description": "..."}]`
- `compare --json` → structured comparison object

**Modified:** `cli.py`

### 6. Customizable System Prompt

New optional config field `agent.system_prompt`:
- Path relative to `.crucible/` (e.g., `system_prompt.md`)
- If unset, use current hardcoded default in `claude_code.py`
- `claude_code.py` reads custom file content at runtime

**Modified:** `config.py` (AgentConfig), `claude_code.py`, `orchestrator.py`

## File Impact Summary

| File | Change Type |
|------|------------|
| `validator.py` | NEW |
| `cli.py` | MODIFY (resume, validate, compare, --json, --verbose) |
| `orchestrator.py` | MODIFY (resume, logging, pass config to agent) |
| `claude_code.py` | MODIFY (custom system prompt, logging) |
| `config.py` | MODIFY (system_prompt field) |
| `results.py` | MODIFY (read_from_git_ref, logging) |
| `guardrails.py` | MODIFY (logging only) |
| `runner.py` | MODIFY (logging only) |
| `git_manager.py` | MODIFY (logging only) |
| `context.py` | MODIFY (logging only) |

## Non-goals

- Agent conversation state persistence (SDK is stateless by design)
- Error context persistence across restarts
- ASCII terminal plots (can be added later)
- Distributed/parallel experiments
