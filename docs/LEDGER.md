# TrialLedger Schema (v1.0)

The TrialLedger is Crucible v1.0's append-only attempt-tree storage,
introduced in M1a. It complements the existing `results-<tag>.jsonl`
(`ResultsLog`) — they coexist; ResultsLog format is unchanged.

## File layout

```
<workspace>/
├── results-<tag>.jsonl          # existing ResultsLog (per-iteration ExperimentRecord)
└── logs/
    └── run-<tag>/
        ├── ledger.jsonl         # NEW v1.0 attempt-tree ledger
        ├── manifest.json        # (M1b) run config snapshot
        └── iter-<N>/            # per-iteration artefacts
            ├── prompt.md        # the agent's exact prompt
            ├── diff.patch       # full diff for commit-bearing nodes
            ├── agent.txt        # agent reasoning output (existing)
            ├── run.log          # captured experiment stdout (existing)
            └── eval-result.json # (M1b) sealed metric artefact
```

## Wire format

Every line of `ledger.jsonl` is a `LedgerRecord` JSON object with `event` ∈
`{"node", "state_update"}`:

### Full node record (`event: "node"`)

```json
{
  "schema_version": 1,
  "event": "node",
  "node": {
    "id": "n000042",
    "uuid": null,
    "parent_id": "n000041",
    "commit": "abc1234",
    "branch": null,
    "backend_kind": "claude_sdk",
    "backend_version": "",
    "model": "anthropic/claude-sonnet-4-6",
    "cli_binary_path": null,
    "cli_version": null,
    "cli_argv": null,
    "env_allowlist": [],
    "prompt_digest": "",
    "prompt_ref": "logs/iter-42/prompt.md",
    "diff_text": "-old\n+new",
    "diff_ref": "logs/iter-42/diff.patch",
    "eval_result_ref": null,
    "eval_result_sha256": null,
    "outcome": "keep",
    "node_state": "frontier",
    "cost_usd": 0.04,
    "usage_source": "api",
    "created_at": "2026-04-25T12:00:00+00:00",
    "expanded_at": null,
    "worktree_path": "/path/to/workspace",
    "description": null
  },
  "node_id": null,
  "node_state": null,
  "created_at": "2026-04-25T12:00:00.001+00:00"
}
```

### State delta record (`event: "state_update"`)

Used by BFTS in M1b+ to mark a node as `expanded` / `pruned` / `exhausted`
without rewriting it.

```json
{
  "schema_version": 1,
  "event": "state_update",
  "node": null,
  "node_id": "n000042",
  "node_state": "expanded",
  "created_at": "2026-04-25T12:01:00+00:00"
}
```

## Field reference (`AttemptNode`)

| Field | Type | Notes |
|---|---|---|
| `id` | str | Short readable id, e.g. `"n000042"` (linear) or `"b1n000042"` (beam 1, iter 42). |
| `uuid` | str \| None | Optional UUID for cross-machine uniqueness. |
| `parent_id` | str \| None | None for root; previous attempt id for descendants. |
| `commit` | str | Git sha. **Empty string** for `violation` / `skip` (no commit happened). |
| `branch` | str \| None | Frontier/expanded nodes MAY have one; renderers must rely on `commit`. |
| `backend_kind` | str | `"claude_sdk"`, `"litellm"`, or `"cli_subscription"`. |
| `backend_version` | str | Adapter version; populated by M2. |
| `model` | str | e.g. `"anthropic/claude-sonnet-4-6"` or `"cli:claude-code"`. |
| `cli_binary_path` | str \| None | Subscription CLI only. |
| `cli_version` | str \| None | `--version` output of the CLI binary. |
| `cli_argv` | list[str] \| None | Exact argv used (for reproducibility). |
| `env_allowlist` | list[str] | Environment variable NAMES exposed (never values). |
| `prompt_digest` | str | sha256 of the full prompt (M1b populates). |
| `prompt_ref` | str | Path to `logs/iter-N/prompt.md`. |
| `diff_text` | str | Inline diff capped at 4 KiB (with `[TRUNCATED]` marker). |
| `diff_ref` | str | Path to full `logs/iter-N/diff.patch`. |
| `eval_result_ref` | str \| None | (M1b) Path to sealed `eval-result.json`. |
| `eval_result_sha256` | str \| None | (M1b) Integrity hash; recompute on read. |
| `outcome` | str | `"keep"`, `"discard"`, `"crash"`, `"violation"`, `"skip"`, `"budget_exceeded"`, `"fatal"`. |
| `node_state` | str | `"frontier"`, `"expanded"`, `"pruned"`, `"exhausted"`. |
| `cost_usd` | float \| None | None when `usage_source == "unavailable"`. |
| `usage_source` | str | `"api"`, `"cli_estimated"`, or `"unavailable"`. |
| `created_at` | str | ISO-8601 UTC. |
| `expanded_at` | str \| None | When BFTS picked this node for expansion. |
| `worktree_path` | str | Workspace path at time of attempt. |
| `description` | str \| None | Capped 500 chars; populated for violation/skip outcomes. |

## Field reference (`EvalResult`) — M1b artefact

Platform-owned sealed metric artefact. Written by Crucible host process,
NEVER by agent code. Read-only / hidden by policy depending on mode.

```json
{
  "schema_version": 1,
  "run_id": "run1",
  "attempt_id": "n000042",
  "commit": "abc1234",
  "eval_command": "python evaluate.py",
  "eval_manifest_hash": "sha256-...",
  "metric_name": "compression_ratio",
  "metric_value": 1.42,
  "metric_direction": "maximize",
  "diagnostics": {"compressed_bytes": 1024, "iterations": 5},
  "valid": true,
  "exit_code": 0,
  "timed_out": false,
  "duration_ms": 1234,
  "stdout_sha256": "...",
  "stderr_sha256": "...",
  "seal": "content-sha256:...",
  "created_at": "2026-04-25T12:00:00+00:00"
}
```

`seal` field upgrades from content-hash (M1) to HMAC-SHA256 (M2) without a
schema_version bump (the format is the same string; the algorithm changes).

## Reading the ledger

```python
from crucible.ledger import TrialLedger

ledger = TrialLedger("logs/run-myrun/ledger.jsonl")

# All nodes (state_updates merged in)
for node in ledger.all_nodes():
    print(node.id, node.outcome, node.cost_usd)

# Tree traversal
for child in ledger.children_of("n000041"):
    print(child.id)

# Best-of-run (requires metric_lookup from results-<tag>.jsonl or EvalResult)
metrics = {"n000001": 0.92, "n000002": 0.88}
best = ledger.best_node(direction="maximize", metric_lookup=metrics)
```

## Reader tolerance

The reader (`TrialLedger.iter_records()`) is strict about middle lines but
tolerates a torn last line: a writer crashing mid-write produces a partial
JSON object on the last line, which reader treats as EOF. Bad lines in the
middle of the file (followed by more content) re-raise — silent corruption
of historical records is treated as a fatal error.

## Concurrency

`TrialLedger.append_node()` uses `fcntl.flock(LOCK_EX)` on POSIX (mac /
Linux) to serialise writers across processes. Windows raises a clear
`RuntimeError` — concurrent ledger writes are unsupported on Windows in
v1.0 (see spec §11 INV-4).

## Wire-compat policy

- Adding optional fields (with sensible defaults) is backward-compatible
  and does NOT bump `schema_version`.
- Renaming, removing, or changing the type of an existing field DOES bump
  `schema_version`.
- Readers MUST raise `UnsupportedSchemaVersion` on unknown major versions
  rather than silently ignoring fields.
