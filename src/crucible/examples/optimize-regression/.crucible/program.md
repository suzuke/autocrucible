# Regression Optimization

You are optimizing a regression model on a synthetic dataset.

## Goal

Minimize `val_mse` — the mean squared error on the validation set.

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- Edit only `model.py`
- Your script must call `evaluate.evaluate(predictions, y_val)` at the end
- The evaluate function prints `val_mse: <value>` — do not modify it
- Training time budget: 60 seconds max
- No GPU required — this is a CPU-only task
- Only use standard library + numpy + scikit-learn

## Data

The dataset is a synthetic regression with 10 features and 10,000 samples:
- 8,000 train / 2,000 validation (fixed split, seed=42)
- The true function has nonlinear interactions between features
- Some features are noisy / irrelevant
