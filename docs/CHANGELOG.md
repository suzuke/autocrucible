# Changelog

## Unreleased — M3 (PRs #2–#11)

The M3 cut: experimental backends + observability improvements. Many of these features are EXPERIMENTAL and gated behind opt-in flags; see linked docs for the truth-in-labeling story per spec §INV-1.

- **Interactive d3 reporter** (M3 PR 15, #9) — `crucible postmortem --html --html-mode=interactive` renders the attempt tree as a self-contained d3.js v7 HTML document with click-to-collapse, pan/zoom, and a details pane. d3 vendored at `_vendor/d3.v7.9.0.min.js` (ISC license preserved). Static mode is byte-identical to before.
- **smolagents AgentBackend** (M2 PR 13, #7) — opt-in via `pip install autocrucible[smolagents]` + `agent.type: smolagents`. Tool surface is the same five-tool registry (Read/Edit/Write/Glob/Grep) enforced by `CheatResistancePolicy` at the tool boundary; no bypass observed across the adversarial test corpus in `tests/security/`.
- **HMAC-SHA256 seal upgrade** (M2 PR 12, #6) — opt-in `eval-result.json` integrity. Default remains `content-sha256`. `verify_seal` is strict-by-default: under HMAC policy, `content-sha256:` seals are rejected (closes a downgrade-attack vector).
- **Reporter compare mode** (M2 PR 11, #5) — `crucible compare a b --html` produces a side-by-side static HTML for two ledgers. Useful for "greedy vs bfts-lite on the same example" demo gates.
- **BFTS doom-loop pruning** (M2 PR 10, #4) — `BFTSLiteStrategy.should_prune()` now actually fires; `decide()` filters pruned candidates before max-metric selection. Kept candidate accumulating ≥3 trailing failed children gets pruned. Demo gate: BFTS reached 30 iter with 6 BranchFrom recoveries vs greedy's 9-iter hard-stop, +11% best metric.
- **Worktree concurrency lock** (M2 PR 14, #8) — `crucible.locking.WorktreeMutex` for future parallel BFTS workers. Lock files live OUTSIDE any workspace at `{tempdir}/crucible-locks/` so the agent's filesystem tools cannot reach them. flock is the authority; sentinel JSON is metadata only. `crucible cleanup --refresh` for stale lock metadata. Windows: explicit warning (cross-process invariant unsupported in v1.0).
- **EXPERIMENTAL — SubscriptionCLIBackend** (M3 PR 16, #10) — wraps subscription CLIs (Claude Code, Codex, Gemini) as agent backends. Per spec §3.3, the CLI is a complete agent product; **Crucible's ACL does NOT constrain it**. Two-flag opt-in required (`experimental.allow_cli_subscription` AND `experimental.acknowledge_unsandboxed_cli`); runs are tagged `isolation=cli_subscription_unsandboxed`; reporter surfaces a warning banner. ClaudeCodeCLIAdapter is implemented; CodexCLIAdapter / GeminiCLIAdapter are stubs gated to PR 16b/c. See [CLI-SUBSCRIPTION-BACKEND.md](CLI-SUBSCRIPTION-BACKEND.md).
- **Polish: ledger query helpers + strategy decision sidecar** (M3 PR 17, #11) — `TrialLedger.kept_path/descendants_of/find_by_outcome` for postmortem / audit tooling. Strategy decisions logged to `logs/run-<tag>/strategy-decisions.jsonl` (separate from ledger to avoid schema bumps). `crucible postmortem --strategy-decisions` prints the recorded sequence.
- **Marketing wording audit** (M3 PR 18, this entry) — README / FAQ / CHANGELOG wording reviewed against spec §INV-1 ("no bypass observed in N adversarial trials" framing, never "secure" / "guaranteed"). Per-backend qualification added to safety claims; new doc [docs/CLI-SUBSCRIPTION-BACKEND.md](CLI-SUBSCRIPTION-BACKEND.md).

## v0.6.0

- **Token Profiling** — `crucible run --profile` tracks per-iteration token usage, prompt section breakdown, and cache hit rates. `crucible postmortem --tokens` renders analysis with bar charts and JSON output. See [docs/PROFILING.md](PROFILING.md).
- **Diff-Based History** — Experiment history now shows actual git diffs instead of agent-generated descriptions. Failed iterations display full code diffs; successful iterations show one-line metric summaries. A/B tested: 42-62% keep rate vs 32% baseline. See [design rationale](DESIGN-DECISIONS.md#diff-based-history-v061).
- **New Example** — `optimize-tsp`: Travelling Salesman Problem with 200 cities, restart strategy, and median-of-3 evaluation.
- **Bug Fixes** — `UsageInfo` deserialization now filters unknown fields (forward compatibility); fixed falsy checks hiding `0`/`0.0` values in profiling output; `_make_record` no longer mutates `agent_result.usage`; cache percentage calculation deduplicated into `UsageInfo.cache_hit_percent()`.

## v0.5.0

- **Search Strategies** — `search.strategy` config key with three modes: `greedy` (default), `restart`, `beam`. Restart resets to baseline after N stagnant iterations; beam maintains K independent branches with cross-beam context sharing.
- **Stability Validation** — `crucible validate` runs the experiment 3× and auto-writes `evaluation.repeat: 3` to config.yaml if CV > 5%. Writes `.crucible/.validated` marker to suppress future hints.
- **Validate Hint** — `crucible run` hints to run `crucible validate` on first iteration when `repeat=1` and not yet validated.

## v0.4.0

- **JSONL Results** — Structured logging with iteration, timestamp, delta, diff stats, and duration per record. Export raw data with `crucible history --format jsonl`.
- **Cost Tracking** — Set `constraints.budget` with `max_cost_usd` and per-iteration limits. `crucible status` shows accumulated cost.
- **Plateau Detection** — Auto-injects a stronger prompt when the metric stagnates for N consecutive iterations. Configure with `constraints.plateau_threshold` (default 8).
- **Eval Quality** — `crucible validate --stability --runs N` checks metric variance across repeated runs. `evaluation.repeat` + `evaluation.aggregation` for multi-run median/mean.
- **Docker Sandbox** — `sandbox.backend: "docker"` runs experiments in containers configured with `network=none`, memory/CPU limits, and read-only root filesystem (configuration enforced by `crucible/sandbox.py` per spec §INV-2 default mandatory configuration).
- **Agent Package Install** — `constraints.allow_install: true` lets the agent edit `requirements.txt`. Auto pip install before execution; Docker mode rebuilds the image with new deps.
- **Per-Iteration Logs** — Agent reasoning saved to `logs/iter-N/agent.txt`, experiment output to `logs/iter-N/run.log`.
- **Agent Abstraction** — `create_agent()` factory and `capabilities()` method. Extension point for future backends.
- **Postmortem Analysis** — `crucible postmortem` with trend chart, `--json` for machine-readable output, `--ai` for Claude-generated insights on turning points and plateaus.

## v0.3.0

- **Interactive Fork Menu** — `crucible run` detects existing branches and offers interactive fork selection.
- **Baseline-Aware Context** — Context assembly includes baseline comparison for new runs.
- **Fork Baseline E2E Test** — Integration test covering the fork workflow.

## v0.2.0

- **Winning Prompt Improvements** — Preamble with role-setting, status icons, Key Lessons section, mandatory workflow in directive, section separators.
- **Hidden Files (v2)** — SDK-level `PreToolUse` hooks deny agent access to hidden files. Replaces the file-move approach from v0.1.0.
- **Skip Loop Protection** — Consecutive skips trigger stop (same as consecutive failures).

## v0.1.0

- Initial release with core loop: assemble prompt, agent edit, guard rails, git commit, run, parse metric, keep/discard.
- Claude Code agent with Read/Edit/Write/Glob/Grep tool allowlist.
- Git branch-per-run strategy with failed-attempt tagging.
- TSV results logging.
- CLI commands: `new`, `init`, `run`, `status`, `history`, `validate`, `compare`.
- Bundled examples: optimize-sorting, optimize-regression, optimize-classifier.
