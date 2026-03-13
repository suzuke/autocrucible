# Fork Baseline Design

## Problem

Each `crucible run` starts independently — run2 has no knowledge of run1's best score. The agent wastes early iterations rediscovering ground already covered. Users who want to build on previous results must manually checkout commits and have no way to set a score baseline.

## Solution: Fork from Previous Run

When starting a new run, crucible detects previous experiments and offers an interactive menu to fork from a prior run's best commit, carrying its score as the initial baseline.

## Resume vs Fork Baseline

These serve different purposes:

| | Resume | Fork Baseline |
|---|---|---|
| **When to use** | Run was interrupted, want to continue | Want a fresh start but keep code gains |
| **Branch** | Same branch, same tag | New branch, new tag |
| **Code state** | Exactly where you left off | Starts from previous run's best commit |
| **History** | Full history preserved, appended to | Clean history, only a baseline record |
| **Program.md** | Same as before (unchanged) | Can be modified before running |
| **Typical scenario** | Laptop closed mid-run, Ctrl+C | Changed strategy/instructions, want to keep code progress |

**Rule of thumb:** If you want to change `program.md` or evaluation approach but keep the code improvements from a previous run, use fork. If you just want to pick up where you left off, use resume.

## User Flow

```
$ crucible run --tag run2

Found previous experiments:
  1) run1  — best: 600.0 (commit abc1234, 15 iters, 8 kept)
  2) run0  — best: 320.0 (commit def5678, 10 iters, 3 kept)
  3) Start fresh

Fork from: [1/2/3] ▸ 1

Forking from run1 best (600.0 @ abc1234)...
Initialised experiment 'run2' in /path/to/project
```

## Changes by Module

### cli.py

In the `run` command, before calling `orch.init()`:

1. Scan for `results-*.tsv` files in project directory (excluding the current tag)
2. For each, parse records and find best kept result
3. If any found, display interactive menu with `click.prompt()`
4. Pass `fork_from=(commit_hash, metric_value, source_tag)` to orchestrator

Add `--no-interactive` flag to skip the menu (for CI/automation), equivalent to "Start fresh".

### git_manager.py

New method: `create_branch_from(tag: str, commit: str)` — checkout the specified commit, then create the experiment branch from there.

### results.py

- `seed_baseline(value: float, source_tag: str)` — write a record with `status=baseline`
- `best()` — treat `baseline` status same as `keep` when finding best
- `is_improvement()` — no change needed (it calls `best()` which now includes baseline)

### orchestrator.py

`init()` accepts optional `fork_from: tuple[str, float, str] | None`:
- If provided: call `git.create_branch_from(tag, commit)` instead of `git.create_branch(tag)`, then `results.seed_baseline(value, source_tag)`
- If None: existing behavior unchanged

### context.py

`_section_state()`: if best record has `status=baseline`, display:
```
Baseline from run1: 600.0 — you must beat this score.
```

## TSV Format

```tsv
commit	metric_value	status	description
abc1234	600.0	baseline	Forked from run1 best
```

The baseline record uses the original commit hash from the source run. This is informational only (the new branch already starts from this commit).

## Edge Cases

- **No previous runs**: No menu shown, direct start fresh (existing behavior)
- **`--no-interactive`**: Skip menu, start fresh
- **Resume same tag**: Existing logic unchanged; baseline record already in TSV
- **Source tag's results file deleted**: That run won't appear in the menu
- **Multiple baselines in TSV**: Only one baseline record should exist (first record); `seed_baseline()` is only called during `init()`

## Impact on Other Commands

- **`crucible status`**: Show baseline record with `(baseline)` label
- **`crucible history`**: Include baseline row, marked distinctly
- **`crucible compare`**: Baseline records filtered or labeled when comparing across tags
