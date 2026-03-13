"""Quantization implementation — edit this file.

Interface:
  quantize(weights: np.ndarray, layer_name: str) -> dict
      Quantize a weight array. Return a dict with at least:
        - 'data': the quantized representation
        - 'bits': average bits per original weight (float)
        - 'layer': layer_name

  dequantize(q: dict) -> np.ndarray
      Reconstruct float32 weights from quantized representation.
      Must return array of same shape and dtype=float32 as original.

Baseline: identity (no quantization) — 32 bits/weight, score = accuracy × 1.0
Goal: reduce bits/weight while keeping accuracy high.
Score = accuracy × (32 / avg_bits_per_weight)

Dependencies: numpy only (no torch, no bitsandbytes)
"""

import numpy as np


def quantize(weights: np.ndarray, layer_name: str) -> dict:
    """Baseline: store as-is (32-bit float, no compression)."""
    return {
        "data": weights.astype(np.float32).copy(),
        "shape": weights.shape,
        "bits": 32.0,
        "layer": layer_name,
    }


def dequantize(q: dict) -> np.ndarray:
    """Reconstruct float32 weights."""
    return q["data"].reshape(q["shape"]).astype(np.float32)
