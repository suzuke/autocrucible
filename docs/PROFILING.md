# Token Profiling

Track prompt composition, cache efficiency, and timing breakdown across experiment iterations.

## Quick Start

```bash
# Run with profiling enabled
crucible run --tag run1 --profile

# Analyze token usage after the run
crucible postmortem --tag run1 --tokens
```

## What It Tracks

| Metric | Source | Description |
|--------|--------|-------------|
| **Prompt breakdown** | Estimated at assembly | Token count per prompt section (instructions, history, state, directive, etc.) |
| **Cache hit rate** | Claude API | `cache_read / (cache_read + cache_creation)` — how much of the prompt is reused |
| **Context utilization** | Claude API | `input_tokens / context_window_limit` |
| **Agent duration** | Wall clock | Time spent in the Claude agent (thinking + tool use) |
| **Run duration** | Wall clock | Time spent executing the experiment (evaluate.py) |
| **SDK timing** | Agent SDK | `duration_ms` and `duration_api_ms` from the SDK response |
| **Num turns** | Agent SDK | Number of agent turns (tool call rounds) per iteration |

## Real-Time Output

With `--profile`, each iteration logs a breakdown line:

```
[profile] prompt: ~557 tok (instructions: 28%, state: 12%, history: 12%, directive: 41%, preamble: 5%) | cache: 90%
```

## Postmortem Analysis

`crucible postmortem --tag run1 --tokens` displays:

```
Token Profile (3 iterations)
===========================================================================
 Iter   In Tok  Out Tok  Cache%  Agent(s)  Run(s)   Status
---------------------------------------------------------------------------
    1       44    10868     90%      85.7     4.6     keep
    2       53     5219     94%      43.5    10.2  discard
    3       30     5461     90%      49.4     4.5     keep
---------------------------------------------------------------------------
  avg       42     7182

Prompt Breakdown (avg tokens per section):
             directive:   233 (34%) ███████████
               history:   174 (25%) ████████
          instructions:   157 (22%) ███████
                 state:    87 (12%) ████
              preamble:    33 ( 4%) █

Cache Efficiency: avg 91% hit rate
```

JSON output is also available:

```bash
crucible postmortem --tag run1 --tokens --json
```

## What to Look For

**History growth** — The history section grows with each iteration (capped at `agent.context_window.history_limit`, default 20). If it dominates the prompt, consider lowering the limit in `config.yaml`:

```yaml
agent:
  context_window:
    history_limit: 10
```

**Low cache hit rate** — If cache % is consistently low, the prompt structure changes too much between iterations. Static sections (instructions, directive) should be cached automatically.

**Agent vs run duration** — If `Agent(s)` is much larger than `Run(s)`, the bottleneck is LLM inference. If `Run(s)` dominates, your experiment evaluation is slow.

**Input tokens near zero** — With high cache hit rates, `In Tok` shows only the *new* (non-cached) tokens. This is expected and means caching is working well.

## Data Storage

All profiling data is stored in the existing `results-{tag}.jsonl` file. New fields are added to each experiment record:

```json
{
  "agent_duration_seconds": 42.1,
  "run_duration_seconds": 3.2,
  "usage": {
    "input_tokens": 44,
    "output_tokens": 10868,
    "cache_read_input_tokens": 8500,
    "cache_creation_input_tokens": 950,
    "prompt_breakdown": {
      "instructions": 157,
      "state": 68,
      "history": 67,
      "directive": 228,
      "preamble": 33,
      "total": 553
    },
    "sdk_duration_ms": 85000,
    "num_turns": 5
  }
}
```

All new fields default to `null` when `--profile` is not used, maintaining full backward compatibility with existing result files.
