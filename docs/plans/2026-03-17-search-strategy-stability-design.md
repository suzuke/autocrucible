# Search Strategy & Stability Check — Design

Date: 2026-03-17

## Background

Analysis of autoresearch issues against Crucible's architecture identified two problems
worth addressing at the framework level:

- **#285 Seed Noise**: Each iteration runs once; stochastic experiments can keep/discard
  based on noise rather than real improvement. Crucible has `evaluation.repeat` and
  `validator.check_stability()` but both are opt-in and not surfaced prominently.

- **#206 DFS Only**: The git strategy always builds on the current best commit (greedy DFS).
  Long runs risk getting stuck in local optima with no structural escape mechanism.
  The existing `--fork` command is manual; `plateau_threshold` warns but doesn't act.

Issues explicitly **not** in scope:

- **#64 Prompt Injection**: Single-user environment, all code user-controlled. Theoretical
  risk only; revisit if multi-user support is added.
- **#294 Multi-metric**: Deliberate design decision (see CONFIG.md#single-metric-by-design).
  Multi-objective optimization belongs in `evaluate.py`.
- **#47 Novelty**: Partially addressed by beam search cross-beam history; no additional
  mechanism needed.

---

## Feature 1: Stability Check in `crucible validate`

### Problem

`evaluation.repeat` defaults to `1`. Users optimizing stochastic ML experiments may
keep commits that only improved due to random seed variance, misleading the agent's
history. `check_stability()` already exists in `validator.py` but is buried and not
run by default.

### Design

**`crucible validate` adds a mandatory stability step:**

1. Run the experiment 3 times (hardcoded, not configurable — keeps validate fast)
2. Compute mean, stdev, CV = stdev / mean × 100
3. Output:
   ```
   Stability check (3 runs): mean=0.842  stdev=0.031  CV=3.7%  ✓ stable
   ```
   or:
   ```
   Stability check (3 runs): mean=0.842  stdev=0.089  CV=10.6%  ⚠ unstable
   → Recommended: evaluation.repeat: 3 (takes median of 3 runs per iteration)
   → Auto-updating config.yaml...
   ```
4. If CV > 5%, automatically set `evaluation.repeat: 3` in `.crucible/config.yaml`
   and write `.crucible/.validated` marker file (gitignored).

**`crucible run` hint:**

On the first iteration, if `evaluation.repeat == 1` and `.crucible/.validated` does
not exist, print once:
```
Tip: Run 'crucible validate' first to check if your metric needs repeat runs.
```

### Affected Files

- `src/crucible/validator.py` — integrate `check_stability()` into main validate flow;
  add auto-write of `repeat` to config and `.validated` marker
- `src/crucible/cli.py` — `validate` command calls updated validator; `run` command
  prints tip on first iteration

---

## Feature 2: Configurable Search Strategy

### Problem

Crucible's git strategy is greedy DFS: always build on the best known commit, reset to
it on failure. After enough failures the `_strategy_hint` escalates to RADICAL, but
this is text guidance only — the agent still starts from the same commit. There is no
structural mechanism to explore a different part of the search space.

The fix must remain **serial** (one agent at a time) to preserve the core design
principle: serial agents learn from history, parallel agents don't.

### Config

New top-level `search` key in `.crucible/config.yaml`:

```yaml
search:
  strategy: greedy        # greedy (default) | restart | beam
  beam_width: 3           # beam only; default 3
  plateau_threshold: 8    # restart + beam: no-improvement iterations before acting
```

Omitting `search` entirely is equivalent to `strategy: greedy` — fully backward-compatible.

`plateau_threshold` moves from `constraints` to `search` (better semantic fit).
The `constraints.plateau_threshold` key will remain supported for one version as an alias
to avoid breaking existing configs.

### Strategy: `greedy` (existing behavior, default)

No change. Always build on current best commit.

### Strategy: `restart`

When the plateau condition is met (`plateau_threshold` consecutive iterations with no
improvement):

1. `git reset --hard <baseline-commit>` (the commit that existed at `crucible init`)
2. Do **not** clear `results.jsonl` — the agent keeps full history as context
3. Inject into context: `⟳ RESTART — previous path exhausted, returning to baseline`
4. Reset `_consecutive_failures` and plateau counter
5. Continue the main loop from the baseline with accumulated knowledge

The agent can see what it already tried (via history table and Key Lessons) and is
expected to explore a structurally different direction.

### Strategy: `beam`

Maintains `beam_width` independent git branches exploring from the same baseline.
The agent runs serially, switching branches in round-robin order.

**Initialization (`crucible init`):**

- Create `beam_width` branches: `{prefix}/{tag}-beam-0`, `{prefix}/{tag}-beam-1`, ...
- All branches start at the same baseline commit
- Create `beam_width` results files: `results-{tag}-beam-{n}.jsonl`
- Primary results file (`results-{tag}.jsonl`) tracks global best across beams

**Main loop (round-robin):**

```
iter 1 → checkout beam-0, agent edits, run, keep/discard
iter 2 → checkout beam-1, agent edits, run, keep/discard
iter 3 → checkout beam-2, agent edits, run, keep/discard
iter 4 → checkout beam-0, ...
```

Each beam maintains its own `_best_commit`, `_consecutive_failures`, and history.

**Cross-beam context (compact):**

Added as a new section in each beam's prompt, after `## Current State`:

```
## Other Beams (read-only)
beam-1  best=1.823  tried: attention heads×2 (✓), dropout 0.3 (💥), residual scaling (✗)
beam-2  best=1.891  tried: layer norm placement (✓), weight tying (✗)
```

Only `description` strings are included (no diffs). Token cost is minimal.

**Global best:**

`results.py` tracks the global best across all beam results files.
`crucible status` shows per-beam and global best.

**Stop condition:**

Run ends when ALL beams have exhausted their `max_retries` consecutive failures,
OR when global `max_iterations` is reached, OR budget is exceeded.

---

## Module Impact Summary

| Module | Changes |
|--------|---------|
| `config.py` | Add `SearchConfig` dataclass; `Config.search`; deprecate `constraints.plateau_threshold` |
| `validator.py` | Add stability check to main flow; auto-write `repeat`; write `.validated` marker |
| `git_manager.py` | Add `create_beam_branches()`, `checkout_beam(beam_id)`, `get_baseline_commit()` |
| `orchestrator.py` | Dispatch to `GreedyOrchestrator`, `RestartOrchestrator`, `BeamOrchestrator`; or strategy pattern within existing class |
| `context.py` | Add `_section_cross_beam_history(other_beams_results)`; pass beam_id to state section |
| `results.py` | Add `beam_id: int | None` to `ExperimentRecord`; add `best_across_beams()` |
| `cli.py` | `run` adds `--strategy` override; `validate` calls new stability flow; first-iter tip |
| `docs/CONFIG.md` | Document `search` block, strategies, beam_width, plateau_threshold migration |
| `docs/FAQ.md` | Update DFS/local-optima answer to mention search strategies |

---

## Non-Goals

- UCB1/MCTS beam selection (round-robin is sufficient and predictable)
- Parallel agents (violates serial-learning design principle)
- Automatic strategy selection (user chooses based on resource budget)
- Cross-beam agent communication beyond compact history summary
