"""Evaluation harness — DO NOT MODIFY.

Generates a fixed synthetic regression dataset and evaluates predictions.

Output format (parsed by crucible):
    val_mse:     <float>
    val_r2:      <float>
    train_mse:   <float>
"""

import numpy as np

SEED = 42
N_SAMPLES = 10_000
N_FEATURES = 10
TRAIN_SPLIT = 8_000


def generate_data():
    """Generate synthetic regression data with nonlinear interactions."""
    rng = np.random.RandomState(SEED)
    X = rng.randn(N_SAMPLES, N_FEATURES)

    # True function: nonlinear with interactions
    y = (
        3.0 * X[:, 0]
        + 1.5 * X[:, 1] ** 2
        - 2.0 * X[:, 2] * X[:, 3]
        + 0.5 * np.sin(X[:, 4] * np.pi)
        + 0.8 * np.abs(X[:, 5])
        + rng.randn(N_SAMPLES) * 0.5  # noise
    )
    # Features 6-9 are irrelevant noise

    X_train, X_val = X[:TRAIN_SPLIT], X[TRAIN_SPLIT:]
    y_train, y_val = y[:TRAIN_SPLIT], y[TRAIN_SPLIT:]
    return X_train, y_train, X_val, y_val


def evaluate(predictions: np.ndarray, y_true: np.ndarray, train_predictions: np.ndarray = None, y_train: np.ndarray = None):
    """Evaluate predictions and print metrics."""
    mse = float(np.mean((predictions - y_true) ** 2))
    ss_res = np.sum((y_true - predictions) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    print(f"val_mse: {mse:.6f}")
    print(f"val_r2: {r2:.6f}")

    if train_predictions is not None and y_train is not None:
        train_mse = float(np.mean((train_predictions - y_train) ** 2))
        print(f"train_mse: {train_mse:.6f}")
