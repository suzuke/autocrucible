# Neural Network Classifier Optimization

You are optimizing a neural network classifier on a synthetic multi-class dataset.

## Goal

Maximize `val_accuracy` — the classification accuracy on the held-out validation set (10,000 samples, 8 classes).

## Hard Rules

- **DO NOT attempt to run or execute any scripts** — the platform runs them automatically
- Edit only `classifier.py`
- Only use numpy — no scikit-learn, no pytorch, no tensorflow
- You may restructure the code however you like, as long as the function signature stays the same

## Function Signature

`train_and_predict(X_train, y_train, X_val)` must return `(val_probs, train_probs)` — both are probability matrices with shape `(N, 8)` where rows sum to 1. The evaluation harness verifies probability validity (shape, sum-to-1, no NaN).

## Data

- 50,000 samples, 20 features, 8 classes
- 40k train / 10k validation (fixed split, seed=42)
- Feature breakdown: 6 informative, 4 derived nonlinear, 10 noise
- Classes defined by concentric rings + angular sectors in feature space — highly nonlinear boundaries
- Slight class imbalance (~1:3 ratio between rarest and most common)
- Features are pre-standardized (zero mean, unit variance on train set)
- Training time budget: 120 seconds max (enforced by platform timeout)
