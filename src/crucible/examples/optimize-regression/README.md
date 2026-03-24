# optimize-regression

Optimize a regression model on a synthetic dataset with nonlinear feature interactions.

**Requirements**: numpy, scikit-learn

## What It Does

- Agent edits `model.py` to build and train a regression model on 10 features with 10,000 samples
- Dataset has nonlinear interactions between features and some noisy/irrelevant features
- Evaluation measures mean squared error on a 2,000-sample validation set (fixed split, seed=42)

## Quick Start

```bash
crucible new my-regression -e optimize-regression
cd my-regression
crucible run --tag v1
```

## Metrics

- **Metric**: val_mse (minimize) -- validation mean squared error
- **Baseline**: varies (depends on initial model complexity)
- **Eval time**: ~5-30s (60s training budget, 120s timeout)
