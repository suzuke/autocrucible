# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

```bash
uv sync                              # install dependencies to .venv
uv run crucible --help               # run CLI from source
uv run pytest                        # run all tests
uv run pytest tests/test_cli.py      # run a single test file
uv run pytest tests/test_cli.py -k test_name  # run a single test
```

Crucible is installed as a global CLI tool via `uv tool install crucible`. Experiment projects have their own separate `pyproject.toml`.

## Architecture

Crucible is an autonomous experiment platform that uses Claude (via Agent SDK) to iteratively optimize a metric through a generate-edit-evaluate loop.

### Core Loop (Orchestrator)

```
assemble prompt (instructions + state + history + errors)
  → agent generates code edits (Claude Agent SDK, tools: Read/Edit/Write/Glob/Grep only)
  → guard rails check (editable files only, valid metrics)
  → git commit
  → run experiment (subprocess with timeout)
  → parse metric from output
  → compare to best → keep or discard (git revert)
  → log to results.tsv
  → loop
```

### Module Responsibilities

- **`cli.py`** — Click CLI: `new`, `init`, `run`, `status`, `history`
- **`orchestrator.py`** — Main loop tying all modules together
- **`agents/base.py`** — Abstract `AgentInterface` + `AgentResult` dataclass
- **`agents/claude_code.py`** — Claude Agent SDK wrapper; strips `CLAUDECODE` env var for nested execution
- **`config.py`** — Loads `.crucible/config.yaml` into typed dataclasses
- **`context.py`** — Assembles dynamic prompts: instructions (program.md) + state + history + error feedback
- **`guardrails.py`** — Validates edits (editable/readonly policy) and metric values (rejects NaN/Inf)
- **`runner.py`** — Subprocess execution with SIGTERM→SIGKILL timeout; metric parsing via regex
- **`git_manager.py`** — Branch creation, commits, failed-attempt tagging, reverts
- **`results.py`** — Append-only TSV log (commit, metric, status, description)

### Git Strategy

- Branch per experiment run: `<prefix>/<tag>`
- Improved iterations keep their commit; failed ones get tagged `failed/<tag>/<seq>` then HEAD is reset
- `results.tsv` is gitignored

### Experiment Project Structure

Each experiment (in `src/crucible/examples/`) follows:
```
.crucible/config.yaml    # metric, commands, file policies
.crucible/program.md     # agent instructions
<editable>.py            # code the agent modifies
<evaluate>.py            # fixed evaluation harness (readonly)
```

Key design principle: architecture constraints must be code-enforced in the evaluation harness, not just stated in program.md (Goodhart's Law prevention).

## Testing Patterns

- Tests use `tmp_path` fixture for isolated git repos
- Agent/subprocess calls are mocked via `unittest.mock.patch`
- `test_integration.py` uses a `FakeAgent` for deterministic full-loop testing
- Helper functions: `setup_repo()`, `make_config()` in test files
