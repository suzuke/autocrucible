# optimize-parameter-golf

Optimize a GPT language model to minimize bits-per-byte (BPB), based on [OpenAI's Parameter Golf](https://github.com/openai/parameter-golf) challenge.

**Requirements**: Mac with Apple Silicon (MLX)

## What's Simplified

This is a demo-friendly version of the full Parameter Golf challenge, designed to run in ~20 minutes on a laptop instead of hours on 8×H100 GPUs.

| | Full Challenge | This Demo |
|--|---------------|-----------|
| Training script | `train_gpt.py` (CUDA) | `train_gpt_mlx.py` (MLX) |
| Training data | 8B tokens (80 shards) | 1M tokens (1 mini shard) |
| Validation set | 62M tokens | 500K tokens |
| Training time | 10 min on 8×H100 | 30s on Apple Silicon |
| Validation time | ~30s on H100 | ~3s on Apple Silicon |
| Per iteration | ~12 min | **~2 min** |
| Model | Same architecture | Same architecture |
| Metric | val_bpb (FineWeb) | val_bpb (FineWeb subset) |

The model architecture, optimizer, quantization pipeline, and evaluation logic are identical — only the data size and compute platform differ. Relative improvement directions (MLP expansion, learning rate tuning, etc.) transfer to the full challenge.

## Quick Start

```bash
crucible new my-parameter-golf -e optimize-parameter-golf
cd my-parameter-golf
bash setup.sh        # downloads MLX script + mini dataset (~3 MB)
crucible run --tag v1
```

## What Happens

- Agent modifies `train_gpt_mlx.py` (architecture, hyperparameters, training strategy)
- Each iteration: ~30s training + ~3s validation = **~35s eval time**
- Baseline BPB: ~3.19 (mini dataset)
- Expected improvement: 3.19 → ~2.0 in ~10 iterations

## Demo Metrics

| Iteration | Time | What to Expect |
|-----------|------|---------------|
| ~2 min | 1 | First improvement (MLP expansion) |
| ~5 min | 3 | Architecture tuning |
| ~15 min | 8 | Hyperparameter refinement |
| ~20 min | 10 | Plateau / convergence |
