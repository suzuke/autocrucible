# optimize-sorting

Optimize a sorting algorithm for 100,000 random integers to maximize throughput.

**Requirements**: None (pure Python, stdlib available)

## What It Does

- Agent edits `sort.py` to implement `sort_array(arr) -> list` that sorts in-place
- Benchmark verifies correctness and measures operations per second
- The key insight: Python's built-in `list.sort()` (Timsort in C) is hard to beat in pure Python

## Quick Start

```bash
crucible new my-sorting -e optimize-sorting
cd my-sorting
crucible run --tag v1
```

## Metrics

- **Metric**: ops_per_sec (maximize) -- sort operations completed per second
- **Baseline**: ~1-2 ops/sec (naive Python sort)
- **Eval time**: ~5-15s (60s timeout)
