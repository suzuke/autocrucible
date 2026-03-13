"""Evaluation harness for quantize.py — DO NOT MODIFY.

Loads model.npz, quantizes all weight matrices, reconstructs them,
runs inference on test set, measures accuracy and compression.

Output format (parsed by crucible):
    score: <float>    (accuracy × 32 / avg_bits, higher = better)
    accuracy: <float>
    avg_bits: <float>
"""

import numpy as np
import sys
import traceback


def relu(x):
    return np.maximum(0, x)


def softmax(x):
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def forward(X, params):
    W1, b1, W2, b2, W3, b3 = params
    h1 = relu(X @ W1 + b1)
    h2 = relu(h1 @ W2 + b2)
    return softmax(h2 @ W3 + b3)


def main():
    try:
        from quantize import quantize, dequantize

        data = np.load("model.npz")
        X_test = data["X_test"]
        y_test = data["y_test"]

        weight_keys = ["W1", "W2", "W3"]
        total_bits = 0.0
        total_params = 0
        reconstructed = {}

        for key in weight_keys:
            w = data[key]
            q = quantize(w.copy(), key)

            if not isinstance(q, dict):
                print(f"ERROR: quantize() must return dict, got {type(q)}")
                print("score: 0.0")
                return
            if "bits" not in q:
                print("ERROR: quantize() dict must contain 'bits' key")
                print("score: 0.0")
                return

            bits = float(q["bits"])
            if bits <= 0 or bits > 32:
                print(f"ERROR: bits must be in (0, 32], got {bits}")
                print("score: 0.0")
                return

            w_reconstructed = dequantize(q)
            if w_reconstructed.shape != w.shape:
                print(f"ERROR: dequantize shape mismatch for {key}: "
                      f"expected {w.shape}, got {w_reconstructed.shape}")
                print("score: 0.0")
                return

            reconstructed[key] = w_reconstructed.astype(np.float32)
            total_bits += bits * w.size
            total_params += w.size

        avg_bits = total_bits / total_params

        params = (
            reconstructed["W1"], data["b1"],
            reconstructed["W2"], data["b2"],
            reconstructed["W3"], data["b3"],
        )
        probs = forward(X_test, params)
        preds = probs.argmax(axis=1)
        accuracy = float((preds == y_test).mean())

        score = accuracy * (32.0 / avg_bits)

        print(f"score: {score:.4f}")
        print(f"accuracy: {accuracy:.4f}")
        print(f"avg_bits: {avg_bits:.4f}")
        print(f"total_params: {total_params}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("score: 0.0")


if __name__ == "__main__":
    main()
