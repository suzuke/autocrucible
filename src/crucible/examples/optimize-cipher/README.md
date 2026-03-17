# optimize-cipher

Demonstrates **restart search strategy** (v0.5.0): after several stagnant iterations, crucible resets to baseline with full history, enabling the agent to try a fundamentally different approach.

## The Problem

Substitution cipher encryption on 1 MB of text. The loop-based baseline can be improved, but hits a hard ceiling. `str.translate()` is a completely different approach that's 5–10× faster — but greedy search won't find it naturally.

## Demo Walkthrough

```bash
crucible run --tag cipher-v1
```

Expected pattern:
1. Iterations 1–4: agent optimizes the loop (comprehension, caching) — reaches ~35 MB/s
2. After `plateau_threshold: 4` stagnant iterations: **restart** resets to baseline
3. Post-restart: agent sees the full history ("loop optimization reached ceiling") and tries `str.translate()`
4. Throughput jumps to 200+ MB/s

## Why Restart Matters

Without restart, the agent would keep refining the loop indefinitely. Restart is not just "retry" — it resets the **code** while retaining **history**, so the agent knows what failed and explores a genuinely different algorithmic family.

## Config

```yaml
search:
  strategy: restart
  plateau_threshold: 4   # short for demo; use 8+ in production
```
