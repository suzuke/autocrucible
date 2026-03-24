# optimize-classifier

Optimize a numpy-only neural network classifier on a synthetic 8-class dataset.

**Requirements**: None (numpy only, no ML frameworks)

## What It Does

- Agent edits `classifier.py` to implement `train_and_predict(X_train, y_train, X_val)` returning probability matrices
- Dataset has 50,000 samples (20 features, 8 classes) with nonlinear boundaries (concentric rings + angular sectors)
- Evaluation measures classification accuracy on a 10,000-sample held-out validation set

## Quick Start

```bash
crucible new my-classifier -e optimize-classifier
cd my-classifier
crucible run --tag v1
```

## Metrics

- **Metric**: val_accuracy (maximize)
- **Baseline**: ~0.125 (random guessing, 8 classes)
- **Eval time**: ~10-60s (120s training budget)
