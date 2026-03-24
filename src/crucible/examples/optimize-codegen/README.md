# optimize-codegen

Optimize a code generator that produces Python solutions for computational tasks, scored on correctness and speed.

**Requirements**: None (pure Python)

## What It Does

- Agent edits `generator.py` to implement `generate(spec) -> str` that returns Python code for each task
- Generated code must assign its answer to a variable named `result`
- Score combines correctness (exact match) and speed ratio (faster than reference = bonus, capped at 10x)

## Quick Start

```bash
crucible new my-codegen -e optimize-codegen
cd my-codegen
crucible run --tag v1
```

## Metrics

- **Metric**: score (maximize)
- **Baseline**: ~1.0 (naive correct implementations)
- **Eval time**: ~5-10s (120s timeout)
