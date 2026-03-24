# optimize-hash

Design a hash function that distributes string keys uniformly across buckets.

**Requirements**: None (pure Python)

## What It Does

- Agent edits `hasher.py` to implement `hash_fn(key, table_size) -> int`
- Cannot use Python's built-in `hash()` or `hashlib` (AST-checked)
- Evaluation hashes 50,000 keys into 65,537 buckets and measures distribution uniformity

## Quick Start

```bash
crucible new my-hash -e optimize-hash
cd my-hash
crucible run --tag v1
```

## Metrics

- **Metric**: uniformity_score (maximize) -- 0.0 (terrible) to 1.0 (perfect)
- **Baseline**: ~0.5 (naive hash)
- **Eval time**: ~2-5s (30s timeout)
