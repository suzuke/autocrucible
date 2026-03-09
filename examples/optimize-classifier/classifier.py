"""Classifier implementation — this is the file the agent optimizes.

Iteration 3: 3 hidden layers (256, 128, 64) + dropout + label smoothing + more epochs.
"""

import numpy as np


def softmax(z):
    z_shifted = z - z.max(axis=1, keepdims=True)
    exp_z = np.exp(z_shifted)
    return exp_z / exp_z.sum(axis=1, keepdims=True)


def gelu(z):
    return 0.5 * z * (1 + np.tanh(np.sqrt(2 / np.pi) * (z + 0.044715 * z ** 3)))


def gelu_grad(z):
    t = np.tanh(np.sqrt(2 / np.pi) * (z + 0.044715 * z ** 3))
    sech2 = 1 - t ** 2
    inner_grad = np.sqrt(2 / np.pi) * (1 + 3 * 0.044715 * z ** 2)
    return 0.5 * (1 + t) + 0.5 * z * sech2 * inner_grad


def build_features(X):
    """Polar coordinates + pairwise interactions on informative features."""
    r = np.sqrt(X[:, 0] ** 2 + X[:, 1] ** 2).reshape(-1, 1)
    theta = np.arctan2(X[:, 1], X[:, 0]).reshape(-1, 1)
    sin_t = np.sin(theta)
    cos_t = np.cos(theta)
    sin_2t = np.sin(2 * theta)
    cos_2t = np.cos(2 * theta)
    x01 = (X[:, 0] * X[:, 1]).reshape(-1, 1)
    x23 = (X[:, 2] * X[:, 3]).reshape(-1, 1)
    x45 = (X[:, 4] * X[:, 5]).reshape(-1, 1)
    r2 = (r ** 2)
    return np.hstack([X[:, :10], r, theta, sin_t, cos_t, sin_2t, cos_2t, x01, x23, x45, r2])


def train_and_predict(X_train, y_train, X_val):
    n_samples = X_train.shape[0]
    n_classes = 8
    rng = np.random.RandomState(123)

    X_train_f = build_features(X_train)
    X_val_f = build_features(X_val)
    n_feat = X_train_f.shape[1]

    # Hyperparameters
    layers = [n_feat, 256, 128, 64, n_classes]
    lr_max = 0.001
    n_epochs = 80
    batch_size = 512
    weight_decay = 1e-4
    dropout_rate = 0.1
    label_smooth = 0.05
    beta1, beta2, eps = 0.9, 0.999, 1e-8

    # Initialize weights
    W, b = [], []
    for i in range(len(layers) - 1):
        W.append(rng.randn(layers[i], layers[i + 1]) * np.sqrt(2.0 / layers[i]))
        b.append(np.zeros(layers[i + 1]))

    n_layers = len(W)
    params = []
    for i in range(n_layers):
        params.extend([W[i], b[i]])

    m_adam = [np.zeros_like(p) for p in params]
    v_adam = [np.zeros_like(p) for p in params]
    t = 0

    n_batches = n_samples // batch_size
    total_steps = n_epochs * n_batches

    for epoch in range(n_epochs):
        perm = rng.permutation(n_samples)
        X_shuf = X_train_f[perm]
        y_shuf = y_train[perm]

        for bi in range(n_batches):
            s, e = bi * batch_size, (bi + 1) * batch_size
            xb, yb = X_shuf[s:e], y_shuf[s:e]
            bs = xb.shape[0]

            step = epoch * n_batches + bi
            lr = lr_max * 0.5 * (1 + np.cos(np.pi * step / total_steps))

            # Forward with dropout
            activations = [xb]
            pre_acts = []
            masks = []
            for i in range(n_layers):
                z = activations[-1] @ W[i] + b[i]
                pre_acts.append(z)
                if i < n_layers - 1:
                    a = gelu(z)
                    # Dropout (training)
                    mask = (rng.rand(*a.shape) > dropout_rate).astype(a.dtype) / (1 - dropout_rate)
                    a = a * mask
                    masks.append(mask)
                    activations.append(a)
                else:
                    activations.append(softmax(z))

            probs = activations[-1]

            # Label smoothing target
            target = np.full((bs, n_classes), label_smooth / n_classes)
            target[np.arange(bs), yb] += 1 - label_smooth

            # Backward
            dz = probs - target
            dz /= bs

            grads = []
            for i in range(n_layers - 1, -1, -1):
                dW_i = activations[i].T @ dz + weight_decay * W[i]
                db_i = dz.sum(axis=0)
                grads = [dW_i, db_i] + grads

                if i > 0:
                    da = dz @ W[i].T
                    da *= masks[i - 1]  # dropout mask
                    dz = da * gelu_grad(pre_acts[i - 1])

            # AdamW update
            t += 1
            for j in range(len(params)):
                m_adam[j] = beta1 * m_adam[j] + (1 - beta1) * grads[j]
                v_adam[j] = beta2 * v_adam[j] + (1 - beta2) * grads[j] ** 2
                m_hat = m_adam[j] / (1 - beta1 ** t)
                v_hat = v_adam[j] / (1 - beta2 ** t)
                params[j] -= lr * m_hat / (np.sqrt(v_hat) + eps)

            for i in range(n_layers):
                W[i] = params[2 * i]
                b[i] = params[2 * i + 1]

    # --- Predict (no dropout) ---
    def predict(X):
        a = X
        for i in range(n_layers):
            z = a @ W[i] + b[i]
            a = gelu(z) if i < n_layers - 1 else softmax(z)
        return a

    return predict(X_val_f), predict(X_train_f)
