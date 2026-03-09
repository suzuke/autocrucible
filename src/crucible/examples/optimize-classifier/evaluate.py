"""Evaluation harness — DO NOT MODIFY.

Generates a synthetic multi-class classification dataset with nonlinear
decision boundaries and evaluates a classifier's predictions.

The dataset has 8 classes arranged in concentric rings and spirals,
making it impossible for linear models and challenging for shallow nets.

- 20 features (6 informative, 4 derived nonlinear, 10 noise)
- 50,000 samples (40k train / 10k validation), fixed seed
- Classes are slightly imbalanced (ratio ~1:3 between rarest and most common)

Output format (parsed by crucible):
    val_accuracy: <float>
    val_loss:     <float>
    train_accuracy: <float>
    elapsed_sec:  <float>
"""

import numpy as np
import time

SEED = 42
N_SAMPLES = 50_000
N_FEATURES = 20
N_CLASSES = 8
TRAIN_SPLIT = 40_000


def generate_data():
    """Generate synthetic classification data with complex boundaries."""
    rng = np.random.RandomState(SEED)

    # --- Core features (6 informative) ---
    X_core = rng.randn(N_SAMPLES, 6)

    # Assign classes using concentric rings + angular sectors
    r = np.sqrt(X_core[:, 0] ** 2 + X_core[:, 1] ** 2)
    theta = np.arctan2(X_core[:, 1], X_core[:, 0])

    # Ring assignment (inner / middle / outer)
    ring = np.digitize(r, bins=[0.8, 1.6]) # 0, 1, 2

    # Angular sector (4 quadrants with shifted boundaries)
    sector = np.digitize(theta, bins=[-np.pi / 2, 0, np.pi / 2])  # 0, 1, 2, 3

    # Combine: 3 rings x 4 sectors = 12 raw classes -> map to 8
    raw_class = ring * 4 + sector
    y = raw_class % N_CLASSES

    # Add class-dependent perturbations using other core features
    for c in range(N_CLASSES):
        mask = y == c
        # Each class has a slightly different manifold
        X_core[mask, 2] += 0.3 * np.sin(c * np.pi / 4) * X_core[mask, 0]
        X_core[mask, 3] += 0.3 * np.cos(c * np.pi / 4) * X_core[mask, 1]

    # --- Derived nonlinear features (4) ---
    X_derived = np.column_stack([
        np.sin(X_core[:, 0] * np.pi),
        X_core[:, 1] * X_core[:, 2],
        np.abs(X_core[:, 3]) * np.sign(X_core[:, 4]),
        np.tanh(X_core[:, 4] + X_core[:, 5]),
    ])

    # --- Noise features (10) ---
    X_noise = rng.randn(N_SAMPLES, 10) * 0.5

    # Combine all features
    X = np.column_stack([X_core, X_derived, X_noise])

    # Shuffle
    perm = rng.permutation(N_SAMPLES)
    X, y = X[perm], y[perm]

    # Standardize features (fit on train only, apply to both)
    mean = X[:TRAIN_SPLIT].mean(axis=0)
    std = X[:TRAIN_SPLIT].std(axis=0) + 1e-8
    X = (X - mean) / std

    X_train, X_val = X[:TRAIN_SPLIT], X[TRAIN_SPLIT:]
    y_train, y_val = y[:TRAIN_SPLIT], y[TRAIN_SPLIT:]

    return X_train, y_train, X_val, y_val


def cross_entropy_loss(probs, y_true):
    """Compute mean cross-entropy loss."""
    n = len(y_true)
    # Clip for numerical stability
    probs_clipped = np.clip(probs[np.arange(n), y_true], 1e-12, 1.0)
    return -np.mean(np.log(probs_clipped))


def evaluate(val_probs, y_val, train_probs=None, y_train=None, elapsed=None):
    """Evaluate predictions and print metrics.

    Args:
        val_probs: (N_val, N_CLASSES) probability matrix
        y_val: (N_val,) true labels
        train_probs: optional (N_train, N_CLASSES) probability matrix
        y_train: optional (N_train,) true labels
        elapsed: optional training time in seconds
    """
    # Validate shapes
    if val_probs.shape != (len(y_val), N_CLASSES):
        print(f"val_accuracy: 0.0")
        print(f"val_loss: 99.0")
        print(f"ERROR: val_probs shape {val_probs.shape} != ({len(y_val)}, {N_CLASSES})")
        return

    # Check probabilities sum to ~1
    row_sums = val_probs.sum(axis=1)
    if not np.allclose(row_sums, 1.0, atol=0.01):
        print(f"val_accuracy: 0.0")
        print(f"val_loss: 99.0")
        print(f"ERROR: probabilities don't sum to 1 (mean sum={row_sums.mean():.4f})")
        return

    # Check for NaN/Inf
    if not np.all(np.isfinite(val_probs)):
        print(f"val_accuracy: 0.0")
        print(f"val_loss: 99.0")
        print("ERROR: val_probs contains NaN or Inf")
        return

    val_preds = np.argmax(val_probs, axis=1)
    val_acc = np.mean(val_preds == y_val)
    val_loss = cross_entropy_loss(val_probs, y_val)

    print(f"val_accuracy: {val_acc:.6f}")
    print(f"val_loss: {val_loss:.6f}")

    if train_probs is not None and y_train is not None:
        train_preds = np.argmax(train_probs, axis=1)
        train_acc = np.mean(train_preds == y_train)
        print(f"train_accuracy: {train_acc:.6f}")

    if elapsed is not None:
        print(f"elapsed_sec: {elapsed:.2f}")

    # Per-class breakdown
    print("\n--- Per-class accuracy ---")
    for c in range(N_CLASSES):
        mask = y_val == c
        if mask.sum() > 0:
            class_acc = np.mean(val_preds[mask] == c)
            print(f"  class_{c}: {class_acc:.4f} (n={mask.sum()})")


if __name__ == "__main__":
    print("Loading data...")
    X_train, y_train, X_val, y_val = generate_data()
    print(f"Train: {X_train.shape}, Val: {X_val.shape}")
    print(f"Classes: {N_CLASSES}, Features: {N_FEATURES}")
    print(f"Class distribution (train): {np.bincount(y_train)}")
    print()

    from classifier import train_and_predict

    t0 = time.perf_counter()
    val_probs, train_probs = train_and_predict(X_train, y_train, X_val)
    elapsed = time.perf_counter() - t0

    print()
    evaluate(val_probs, y_val, train_probs, y_train, elapsed)
