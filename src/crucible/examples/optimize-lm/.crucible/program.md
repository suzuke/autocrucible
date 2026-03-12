# Language Model Optimization

## Goal

Minimize `val_bpb` (validation bits per byte). Lower means the model predicts held-out text better.

## Hard Rules

- Edit only `train.py`
- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- Only use `mlx`, `mlx.nn`, `mlx.optimizers`, `numpy`, and Python stdlib — no PyTorch, no TensorFlow
- Each run has a **6-minute wall-clock budget** (enforced by platform timeout)
- Make focused, coherent changes per iteration — do NOT rewrite the entire model from scratch

## Interface Requirements

- `build_model()` must return an `mlx.nn.Module`
- `train(model, train_data, val_data, vocab_size)` must return the trained model
- The evaluation harness calls these functions and measures val_bpb — you cannot tamper with measurement

## Data

- ~1.1M characters of English text (TinyShakespeare)
- Character-level tokenization (vocabulary ~65 unique chars)
- 90/10 train/val split, fixed seed
- Context window defined in train.py (default 256 characters)

## MLX-Specific Notes

- MLX optimizers accept a callable LR schedule: `optim.AdamW(learning_rate=schedule_fn)` where `schedule_fn` takes step -> lr. Use `optim.cosine_decay`, `optim.linear_schedule`, or similar built-in schedules.
- Do NOT try to mutate `optimizer.learning_rate` at each step — use the schedule function approach instead.
- `mx.eval(model.parameters(), optimizer.state)` must be called after each update step.
- MLX lazy evaluation: computations are only executed when `mx.eval()` is called.

## Strategy

- Read train.py and evaluate.py to understand the current architecture and training setup
- Analyze previous iteration results to understand what worked and what didn't
- Make targeted improvements based on your analysis — architecture, optimization, regularization, or training strategy
- If a change made things worse, understand why before trying something different
