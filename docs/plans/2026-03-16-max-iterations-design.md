# Design: --max-iterations

## Problem

`crucible run` loops indefinitely until Ctrl+C or budget/failure limits. Users (and agents) frequently want `--max-iterations N` to bound runs for testing or controlled execution.

## Design

Add `max_iterations` as both a config field and CLI flag.

### Config

```yaml
constraints:
  max_iterations: null  # default: null = unlimited
```

### CLI

```
crucible run --tag x --max-iterations 10
```

CLI value overrides config. If neither is set, behavior unchanged (infinite loop).

### Orchestrator

`run_loop()` accepts `max_iterations: int | None`. Each iteration increments a local counter. When counter reaches limit: `logger.info("Reached max iterations (N), stopping.")` then break. Exit code 0.

### Resume behavior

Counter tracks iterations **in this run session**, not historical total. `--max-iterations 2` means "run 2 more iterations from here."

### What this does NOT do

- No new status type (unlike `budget_exceeded`)
- No agent notification — agent does not know about the iteration limit
- No changes to context assembly

## Files to change

1. `config.py` — add `max_iterations: int | None = None` to `ConstraintsConfig`, parse from YAML
2. `cli.py` — add `--max-iterations` option to `run` command, pass to `run_loop()`
3. `orchestrator.py` — `run_loop(max_iterations=None)`, add counter + break condition
4. Tests — add cases for config parsing, CLI flag, orchestrator loop termination
