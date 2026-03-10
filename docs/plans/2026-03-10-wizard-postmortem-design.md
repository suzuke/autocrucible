# Wizard + Postmortem Design

Date: 2026-03-10

## Goal

Add two features to reduce experiment setup friction and improve result analysis:
1. `crucible wizard` — interactive AI-powered experiment scaffolding
2. `crucible postmortem` — AI-powered experiment analysis

## Feature 1: `crucible wizard`

### UX Principle

- User provides ONE natural language description
- Claude infers everything it can confidently determine
- For genuinely ambiguous decisions: ask via multiple-choice with plain-language explanations
- Max 3 questions; each option explains the impact in non-technical terms
- Never ask open-ended technical questions

### CLI Interface

```
crucible wizard <dest> [--describe "..."]
```

- `--describe`: inline description, skip interactive input prompt
- Without `--describe`: prompt for description, then ask follow-up questions

### Example Flow

```
$ crucible wizard my-sorting-experiment

🧪 Crucible Experiment Wizard

What do you want to optimize? (describe in plain language)
> 寫一個最快的純 Python 排序演算法，不能用內建 sort

Got it! I have a few questions:

[1/2] How should we measure "fast"?

  1. Elements sorted per second — measures raw throughput
  2. Time to sort 10,000 elements — measures latency on fixed input
  3. Time to sort varying sizes (100~100K) — tests scalability across scales

  Pick [1/2/3]: 1

[2/2] How strict should correctness checking be?

  1. Basic — just check output is sorted
  2. Strict — check sorted + same elements + stable order
  3. Adversarial — add edge cases (empty, duplicates, already sorted, reverse)

  Pick [1/2/3]: 3

Generating experiment...

✓ Generated:
  Metric:      throughput (elements/sec, higher = better)
  Correctness: adversarial (empty, duplicates, sorted, reverse)
  Agent edits: solution.py
  Timeout:     60s

  cd my-sorting-experiment
  crucible init --tag run1 && crucible run --tag run1
```

### Implementation

Two-phase Claude interaction:

1. **Analyze phase**: send description → Claude returns JSON with `{inferred: {...}, uncertain: [{param, choices: [{label, explanation}]}]}`
2. **Generate phase**: send inferred + user choices → Claude returns JSON with all file contents + summary

Module: `wizard.py`
- Parses Claude's analysis response
- Presents choices via `click.prompt` with numbered options
- Calls Claude again with all decisions
- Writes files to disk
- Prints summary

Generated files include Goodhart prevention guards in evaluate.py (correctness validation, not just metric).

### Generation Prompt Core

```
You are generating a crucible experiment project. Given the user's description:
"{description}"

Phase 1 — Analyze:
Return JSON: {inferred, uncertain} where uncertain items have choices with plain-language explanations.

Phase 2 — Generate files:
1. config.yaml — standard crucible format
2. program.md — clear instructions for the optimization agent
3. evaluate.py — readonly harness with correctness validation + metric output
4. solution.py — minimal baseline implementation
5. .gitignore

Principles:
- evaluate.py must be READONLY and tamper-resistant
- Metric must be deterministic (fixed seed, fixed input)
- Include edge case testing in evaluate.py
```

## Feature 2: `crucible postmortem`

### CLI Interface

```
crucible postmortem --tag <tag> [--project-dir .] [--no-ai] [--json]
```

- `--no-ai`: data-only mode, no Claude API call
- `--json`: structured output

### Three-Layer Architecture

| Layer | Content | Needs Claude? |
|-------|---------|---------------|
| **Data layer** | metric trend, keep/discard/crash stats, failure streaks | No |
| **Diff layer** | git diff summary for each kept iteration | No |
| **Insight layer** | pattern analysis, crash explanations, recommendations | Yes |

### `--no-ai` Output (Free)

- ASCII metric trend bar chart
- Statistics summary (total, kept, discarded, crashed, keep rate)
- Diff stat for each kept commit (files changed, lines added/removed)
- Consecutive failure streak annotations

### AI Insight Output (Default)

On top of `--no-ai` data, sends everything to Claude once for:
- Identifying turning points (which change caused qualitative leaps)
- Explaining failure patterns (why consecutive crashes)
- Detecting plateaus
- Suggesting next directions

### Example Output

```
$ crucible postmortem --tag run1

🔍 Analyzing experiment 'run1' (23 iterations)...

## Summary
  Best: 142,000 elem/sec (iter 18)
  Kept: 8/23 (35%)  |  Discarded: 10  |  Crashed: 5

## Metric Trend
  iter  1 ████░░░░░░░░░░░░  42,000   keep   baseline bubble sort
  iter  2 ████░░░░░░░░░░░░  38,000   discard tried merge sort but bug
  iter  3 ██████░░░░░░░░░░  67,000   keep   fixed merge sort
  ...
  iter 18 ████████████████ 142,000   keep ★  hybrid intro+insertion sort

## Key Insights

  1. Turning point at iter 8: switching from O(n²) to O(n log n)
     doubled throughput.

  2. Crash cluster iter 5-7: agent tried to import numpy —
     blocked by eval harness. Learned after 3 failures.

  3. Plateau at iter 14-17: micro-optimizations with <2% gains.
     Breakthrough from hybrid algorithm.

  4. Recommendation: hasn't tried timsort-style natural merge sort.
```

### Implementation

Module: `postmortem.py`
- Read results.tsv
- Git log + git diff for each kept commit
- Compute stats + ASCII trend chart
- If not `--no-ai`: call Claude with all data → get insights
- Render to terminal (or `--json`)

## File Impact

### New Files

| File | Responsibility |
|------|---------------|
| `wizard.py` | Interactive Q&A + Claude calls + file generation |
| `postmortem.py` | Data analysis + diff collection + AI insights |
| `test_wizard.py` | Mock Claude, verify file generation + interaction flow |
| `test_postmortem.py` | Fake results.tsv + git repo for data layer; mock Claude for insights |

### Modified Files

| File | Change |
|------|--------|
| `cli.py` | Add `wizard` and `postmortem` subcommands |

### Untouched (core loop)

`orchestrator.py`, `agents/`, `runner.py`, `guardrails.py`, `config.py`, `context.py`, `git_manager.py`, `results.py` — no changes.

## Non-Goals

- Do not modify `crucible new` — wizard is a separate command
- No web dashboard — terminal + `--json` is sufficient
- No live monitoring — postmortem is post-hoc analysis
- No cross-experiment comparison — `crucible compare` already exists
- No new package dependencies — uses existing `claude_agent_sdk`

## Testing Strategy

- `test_wizard.py`: mock Claude calls, verify correct file generation and interactive flow
- `test_postmortem.py`: fake results.tsv + git repo for data layer; mock Claude for insight layer
