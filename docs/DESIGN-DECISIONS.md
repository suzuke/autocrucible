# Design Decisions

Records the reasoning and evidence behind significant architectural changes.

## Diff-Based History (v0.6.1)

### Problem

Agent-generated descriptions in experiment history were unreliable. Common failures:

- Markdown headings leaking as descriptions: `## Step 4: EXPLAIN`
- Self-verification text: `Perfect! The code is clean and syntactically correct.`
- Descriptions duplicated in both the history table AND Key Lessons section
- History growing to 63% of prompt by iteration 30 (mostly verbose descriptions)

Root cause: `_clean_description()` extracted the first line of the agent's last text block, which was often not the actual description.

### Solution

Replace agent-generated descriptions with git diffs in the history section:

1. After each commit, capture a compact diff (changed lines only, no context headers)
2. In history, show **full diff for failed iterations** (discard/crash) — agent needs to see exactly what didn't work
3. Show **one-line metric summary for successful iterations** — the improvement is already reflected in the current code, no need to repeat
4. Only the **most recent 5 failed diffs** are shown in full; older failures get a one-line summary

### Why not truncate diffs?

First attempt used 200-char truncation, but A/B test showed no improvement over descriptions — truncated diffs are just as useless as truncated descriptions. The agent needs the full picture to avoid repeating mistakes.

### Why only show failures?

Successful changes are already in the codebase. The agent can read the current code to see what worked. History's core value is telling the agent **what NOT to try again**.

### A/B Test Results (sorting benchmark, 19-20 iterations each)

| Metric | Diff History (v0.6.1) | Description History (old) |
|--------|----------------------|--------------------------|
| Keep rate | **42-62%** | 32% |
| History size | ~466 tok (avg) | ~623 tok (avg) |
| Crash rate | 0 | 1 |
| Cost | ~$1.00 | ~$0.95 |

Additional validation on snake benchmark:
- **62% keep rate** (5/8) with 5 consecutive keeps at start
- Previous best on snake: 40-44% keep rate

### Key Insight

> Diff-based history helps the agent improve **faster and more consistently** (higher keep rate, consecutive keeps). It does NOT guarantee a higher final metric — that depends more on iteration count and search strategy.

### Files Changed

- `git_manager.py` — `compact_diff()` method
- `results.py` — `diff_text` field on `ExperimentRecord`
- `orchestrator.py` — capture diff after each commit
- `context.py` — `_section_history()` rewritten: failures with diff blocks, keeps as one-liners
