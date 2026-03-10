# Language Model Optimization (autoresearch-style)

You are optimizing a ~4.8M parameter character-level GPT language model, trained on Apple Silicon GPU via MLX.

This experiment recreates the core idea of Karpathy's autoresearch: iteratively improve a language model's validation performance through architecture and hyperparameter changes, within a fixed compute budget.

## Goal

Minimize `val_bpb` — validation bits per byte. Lower means the model predicts held-out text better.

## Rules

- Edit only `train.py`
- Your `build_model()` function must return an `mlx.nn.Module`
- Your `train(model, train_data, val_data, vocab_size)` function must return the trained model
- The evaluation harness calls these functions and measures val_bpb — you cannot tamper with measurement
- Each run has a **6-minute wall-clock budget** (enforced by platform timeout)
- Only use `mlx`, `mlx.nn`, `mlx.optimizers`, `numpy`, and Python stdlib — no PyTorch, no TensorFlow
- You may restructure the code however you like, as long as function signatures stay the same
- **Make focused, coherent changes per iteration.** You may change 2-3 related things together (e.g., increase steps + add LR schedule + tune weight decay), but do NOT rewrite the entire model architecture from scratch. If a change makes things worse, analyze why before trying something else.

## Data

- ~1.1M characters of English text (full TinyShakespeare)
- Character-level tokenization (vocabulary ~65 unique chars)
- 90/10 train/val split, fixed seed
- Context window defined in train.py (default 256 characters)

## What You Can Try

### Optimization (start here — biggest bang for the buck)
- Learning rate schedule: cosine decay with warmup (use `optim.cosine_decay`)
- AdamW with proper weight decay (0.01-0.1)
- More training steps (baseline only does 500 — you have budget for 5000+)
- Gradient clipping
- Batch size tuning (smaller batch = more steps per time budget)

### Architecture
- Depth (6→8→10 layers)
- Width (256→384 embedding dim)
- Different head counts (8→12)
- SwiGLU/SiLU MLP activation (used in LLaMA/Mistral)
- RoPE positional encoding
- Weight tying (share token_emb and head weights)
- Grouped query attention (GQA)

### Training Strategy
- Context window tuning (256→384→512)
- Gradient accumulation for effective larger batch
- Label smoothing
- Multiple training phases (high LR → low LR)

### Regularization
- Dropout on attention and MLP (0.05-0.2)
- Weight decay on embeddings (separate from other params)
- Embedding dropout

## MLX-Specific Notes

- MLX optimizers accept a callable LR schedule: `optim.AdamW(learning_rate=schedule_fn)` where `schedule_fn` takes step → lr. Use `optim.cosine_decay`, `optim.linear_schedule`, or similar built-in schedules.
- Do NOT try to mutate `optimizer.learning_rate` at each step — use the schedule function approach instead.
- `mx.eval(model.parameters(), optimizer.state)` must be called after each update step.
- MLX lazy evaluation: computations are only executed when `mx.eval()` is called.

## Tips

- Baseline is a 6-layer, 8-head, 256-dim GPT (~4.8M params) with vanilla Adam, 500 steps — intentionally under-trained
- **The baseline runs in ~30s. You have 360s budget. Low-hanging fruit: increase MAX_STEPS to 3000-5000 with cosine LR decay.**
- **Start with optimization improvements BEFORE changing architecture**
- Increasing steps alone may not help if LR is too high — combine more steps with cosine LR decay
- If you increase model size, you MUST increase training steps proportionally
- A well-trained 4.8M model beats an under-trained 10M model every time
- Pre-norm (LayerNorm before attention/MLP) is already used in the baseline
- GELU activation is already used — good default, but SwiGLU can be better
- With good optimization, val_bpb below 1.4 is achievable on this dataset
- Watch training loss vs val loss — if training loss << val loss, add regularization
- Weight tying (share embedding and output weights) saves params and often improves generalization
