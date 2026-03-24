# optimize-parameter-golf

Optimize a GPT language model to minimize bits-per-byte (BPB), based on [OpenAI's Parameter Golf](https://github.com/openai/parameter-golf) challenge.

**Requirements**: Mac with Apple Silicon (MLX)

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
