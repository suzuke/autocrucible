# optimize-lm

Optimize a character-level language model on TinyShakespeare to minimize bits per byte.

**Requirements**: Mac with Apple Silicon (MLX)

## What It Does

- Agent edits `train.py` to improve model architecture, training strategy, and hyperparameters
- Model uses MLX framework with character-level tokenization (~65 unique chars) on ~1.1M characters of Shakespeare
- Evaluation measures validation bits-per-byte (lower = better prediction of held-out text)

## Quick Start

```bash
crucible new my-lm -e optimize-lm
cd my-lm
crucible run --tag v1
```

## Metrics

- **Metric**: val_bpb (minimize) -- validation bits per byte
- **Baseline**: ~2.0+ (simple model)
- **Eval time**: ~3-6 min (360s timeout, 6-minute wall-clock budget)
