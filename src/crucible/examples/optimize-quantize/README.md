# optimize-quantize

Implement post-training quantization to compress a neural network, balancing accuracy vs compression ratio.

**Requirements**: None (numpy only)

## What It Does

- Agent edits `quantize.py` to implement `quantize(weights, layer_name)` and `dequantize(q)` functions
- Score formula: `accuracy x (32 / avg_bits_per_weight)` -- fewer bits means higher multiplier but worse accuracy
- Strategies range from INT8 (4x multiplier) to INT4 (8x multiplier) with per-channel or mixed precision

## Quick Start

```bash
crucible new my-quantize -e optimize-quantize
cd my-quantize
crucible run --tag v1
```

## Metrics

- **Metric**: score (maximize) -- accuracy x compression multiplier
- **Baseline**: ~1.0 (no quantization, 32-bit, 1x multiplier)
- **Eval time**: ~5-15s (60s timeout)
