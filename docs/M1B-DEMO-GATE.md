# M1b Demo Gate — Real-Agent Sanity (BFTS vs greedy)

**Date**: 2026-04-25
**Spec reference**: `docs/v1.0-design-final.md` §M1b deliverable demo gate
**Cost (live LLM via Claude Code subscription)**: ~$0.45 for 6 iterations × ~30 minutes wall

## TL;DR

Real-agent runs on `optimize-compress` (and `optimize-2048`, see §3) demonstrate end-to-end M1b behaviour:

- ✅ `SearchStrategy` Protocol drives the loop (BFTS-lite seen in CLI logs as `[strategy] max_iterations=3 reached`)
- ✅ `BranchFrom` doesn't fire in 3-iter sanity runs (correct — only one kept node, nothing to branch from)
- ✅ **`parent_id = code ancestry` (PR 8a) is observable in real LLM runs** — discard nodes do not become parents for subsequent attempts
- ✅ Sealed `EvalResult` artefacts written per iteration with `eval_manifest_hash` + `stdout_sha256` + `seal: content-sha256:...`
- ✅ Static HTML postmortem renders without errors for both strategies

This is a **sanity check, not a metric-superiority claim**. 3 iterations is too short to exercise BFTS branching meaningfully. M2 doom-loop pruning + 30+ iter runs are what would produce statistically meaningful "BFTS beats greedy" data.

## 1. Setup

Two parallel workspaces created from the bundled `optimize-compress` example:

```bash
crucible new compress-greedy -e optimize-compress
crucible new compress-bfts   -e optimize-compress
# Override search.strategy to "bfts-lite" in compress-bfts/.crucible/config.yaml
```

Both workspaces validated via `crucible validate`:

```
[PASS] Editable files: All files exist
[PASS] Run command: Executed successfully
[PASS] Eval/metric: compression_ratio: 0.5122  (baseline)
[PASS] Stability: CV=0.0%  mean=0.5122  ✓ stable
```

Strategy chosen via `config.search.strategy` field (greedy is default; bfts-lite is the M1b addition).

## 2. optimize-compress results

### Greedy (3 iter, `--tag demo`)

| Iter | Outcome | Cost | Best metric | Tokens (in/out) |
|------|---------|------|-------------|------------------|
| 1 | keep | $0.0794 | 0.0 | 46 / 4254 |
| 2 | keep | $0.1065 | **1.7973** | 81 / 8962 |
| 3 | discard | $0.0620 | 1.7973 | 53 / 5345 |

**Final best**: `compression_ratio: 1.7973` (3.5× the 0.5122 baseline)
**Total cost**: $0.2479
**Wall time**: ~3 min

Ledger:
```
n000001 keep    parent=None       commit=e5bcc27  cost=$0.079
n000002 keep    parent=n000001    commit=9a3673a  cost=$0.107
n000003 discard parent=n000002    commit=1f482ec  cost=$0.062
```

### BFTS-lite (3 iter, `--tag demo`)

| Iter | Outcome | Cost | Best metric | Tokens (in/out) |
|------|---------|------|-------------|------------------|
| 1 | keep | $0.0484 | 0.9768 | 46 / 4387 |
| 2 | discard | $0.0863 | 0.9768 | 44 / 9927 |
| 3 | keep | $0.0721 | **1.7074** | 51 / 6401 |

CLI log shows: `[strategy] max_iterations=3 reached` — confirms `SearchStrategy.decide()` is being called by the orchestrator.

**Final best**: `compression_ratio: 1.7074`
**Total cost**: $0.2068
**Wall time**: ~4 min

Ledger:
```
n000001 keep    parent=None       commit=4612a6b  cost=$0.048
n000002 discard parent=n000001    commit=1c52ef0  cost=$0.086
n000003 keep    parent=n000001 ⚡   commit=fa4e065  cost=$0.072
                ↑ NOT n000002 — code-ancestry semantics in action
```

### The parent_id observation

Compare the two ledgers' iter-3 entries:

| Strategy | iter 3 outcome | iter 3 parent_id |
|----------|----------------|-------------------|
| Greedy | discard | `n000002` (the previous kept) |
| BFTS-lite | keep | `n000001` (the kept ancestor, NOT the discarded `n000002`) |

PR 8a's "code ancestry" fix is observable here: after BFTS's iter-2 discard, `git reset_to_commit` reverted the workspace to `n000001`'s state, so iter 3 actually started from there — and the ledger correctly records that. This is the semantic that lets BFTS read the ledger and reason about "where each attempt came from in the code history."

The HTML postmortem renders this difference: greedy shows a strict descending chain; BFTS shows iter-2 as a sibling branch off iter-1.

## 3. optimize-2048 results

Same setup pattern as §2: two parallel workspaces, one with greedy, one with bfts-lite. Baseline `avg_score: 1344.8` (CV=6.6%, auto-set `evaluation.repeat=3` for stability).

### Greedy (3 iter)

| Iter | Outcome | Cost | Best avg_score | Tokens (in/out) |
|------|---------|------|----------------|------------------|
| 1 | keep | $0.0458 | 3270.6 | 51 / 1818 |
| 2 | keep | $0.0509 | 5605.4 | 44 / 3982 |
| 3 | keep | $0.0565 | **8921.2** | 37 / 5188 |

**Final**: 8921.2 (6.6× baseline). Strict monotonic improvement → all 3 iter kept.
**Total cost**: $0.1532

### BFTS-lite (3 iter)

| Iter | Outcome | Cost | Best avg_score | Tokens (in/out) |
|------|---------|------|----------------|------------------|
| 1 | keep | $0.0444 | 2164.0 | 37 / 4120 |
| 2 | keep | $0.0528 | 2966.0 | 37 / 4361 |
| 3 | keep | $0.0658 | **7595.4** | 30 / 6732 |

**Final**: 7595.4 (5.6× baseline). Also strict monotonic.
**Total cost**: $0.1630

### Why both ledgers are linear chains

Both strategies produced identical-shape ledgers (n1 → n2 → n3, all keep):

```
GREEDY:    n000001 keep  parent=None      → n000002 keep  parent=n1  → n000003 keep  parent=n2
BFTS-LITE: n000001 keep  parent=None      → n000002 keep  parent=n1  → n000003 keep  parent=n2
```

The reason: BFTSLiteStrategy.decide() returns `Continue` when "most-recent ledger node is already a child of the metric-best kept node." With strict monotonic improvement, the most recent IS always the best, so BFTS has nothing to branch FROM. Verified algorithmically; this is not a bug, it's the correct decision in this scenario.

To exercise BranchFrom in a real-agent run, the agent needs to produce regressions interspersed with improvements. The optimize-compress run (§2) demonstrated this with iter-2 discard followed by iter-3 keep that correctly chained back to iter-1's commit.

## 3a. Cumulative cost across both examples

| Example | Strategy | Iters | Total cost | Wall time |
|---------|----------|-------|-----------|-----------|
| optimize-compress | greedy | 3 | $0.2479 | ~3 min |
| optimize-compress | bfts-lite | 3 | $0.2068 | ~4 min |
| optimize-2048 | greedy | 3 | $0.1532 | ~2 min |
| optimize-2048 | bfts-lite | 3 | $0.1630 | ~3 min |
| **Total** | | **12** | **$0.7709** | **~12 min** |

All runs used the user's CC subscription via Claude Code SDK. No OpenRouter / API key tokens consumed.

## 4. What this validates

- **End-to-end wiring** — `crucible run` with `strategy: bfts-lite` produces a clean run, no crashes, no Python errors.
- **Strategy seam is live** — `[strategy] max_iterations=3 reached` log line proves `BFTSLiteStrategy.decide()` is the deciding voice (not the legacy string-branching path).
- **Sealed artefacts** — every iteration directory contains `eval-result.json` with the `seal: "content-sha256:..."` field populated; AttemptNode references it via `eval_result_ref` + `eval_result_sha256`.
- **Code-ancestry parent chain** — verifiably different from sequence-based parent chain in the BFTS run.
- **HTML reporter** — generates valid HTML for both runs, including the tree-view structure for BFTS.

## 5. What this does NOT validate

- **"BFTS beats greedy" superiority**. Both runs hit similar metrics (1.80 vs 1.71) in 3 iters. With one kept node and no clear divergent paths to expand, BFTS-lite returned `Continue` rather than `BranchFrom`. M2 needs 30+ iter runs and a domain with multiple promising branches to show empirical advantage.
- **Long-tail BFTS robustness**. The reviewer's F2 fix (BranchFrom pre-empts max_retries) is exercised in the dedicated `test_branch_from_preempts_max_retries` regression test, but not yet in a real-agent run with mixed outcomes.
- **Doom-loop / pruning behaviour**. M1b BFTS-lite always picks the metric-best; M2 will plug in `should_prune` so repeated futile expansions get filtered.

## 6. Operational notes

- Both runs used the user's CC subscription (Claude Code) — no API key required, but each run consumed daily-budget tokens.
- Demo workspaces live at `~/Documents/Hack/crucible-demo-gate/`.
- HTML reports generated via `crucible postmortem --tag demo --html --no-ai`.
- The `--no-ai` flag is essential for demo-gate runs to avoid an extra round-trip to the AI insights generator (which would consume more quota for marginal value here).

## 7. Next steps (M2 prep)

- Run with `--max-iterations 30` to give BFTS room to actually diverge.
- Compare per-iteration cost trajectory: BFTS should retain alternative kept nodes for later expansion, while greedy commits monotonically.
- Add a `crucible compare` mode that side-by-sides two ledgers' HTML reports.
