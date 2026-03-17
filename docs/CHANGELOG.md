# Changelog

## v0.5.0

- **Search Strategies** — `search.strategy` config key with three modes: `greedy` (default), `restart`, `beam`. Restart resets to baseline after N stagnant iterations; beam maintains K independent branches with cross-beam context sharing.
- **Stability Validation** — `crucible validate` runs the experiment 3× and auto-writes `evaluation.repeat: 3` to config.yaml if CV > 5%. Writes `.crucible/.validated` marker to suppress future hints.
- **Validate Hint** — `crucible run` hints to run `crucible validate` on first iteration when `repeat=1` and not yet validated.

## v0.4.0

- **JSONL Results** — Structured logging with iteration, timestamp, delta, diff stats, and duration per record. Export raw data with `crucible history --format jsonl`.
- **Cost Tracking** — Set `constraints.budget` with `max_cost_usd` and per-iteration limits. `crucible status` shows accumulated cost.
- **Plateau Detection** — Auto-injects a stronger prompt when the metric stagnates for N consecutive iterations. Configure with `constraints.plateau_threshold` (default 8).
- **Eval Quality** — `crucible validate --stability --runs N` checks metric variance across repeated runs. `evaluation.repeat` + `evaluation.aggregation` for multi-run median/mean.
- **Docker Sandbox** — `sandbox.backend: "docker"` runs experiments in isolated containers with network isolation, memory/CPU limits, and readonly filesystem protection.
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
