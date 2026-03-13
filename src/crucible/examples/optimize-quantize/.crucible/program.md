# Quantization Optimization

You are implementing post-training quantization to compress a neural network.

## Goal

Maximize `score = accuracy × (32 / avg_bits_per_weight)`.
- Higher compression (fewer bits) = higher multiplier
- But compression hurts accuracy
- Find the best accuracy/compression trade-off

## Interface

```python
def quantize(weights: np.ndarray, layer_name: str) -> dict:
    # Returns dict with 'data', 'bits' (float), 'layer', and any fields dequantize needs

def dequantize(q: dict) -> np.ndarray:
    # Reconstructs float32 array of same shape as original weights
```

## Rules

- Edit only `quantize.py`
- numpy only — no torch, bitsandbytes, or other ML libraries
- `dequantize(quantize(w))` must return array of same shape as `w`
- `bits` must be a float in (0.0, 32.0]

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `quantize.py`

## Model Info

See `model_info.txt` for layer shapes and expected score ranges.
Baseline (no quantization, 32-bit): score ≈ accuracy (1x multiplier)

## Strategy

Common quantization approaches:
- **INT8 symmetric**: scale weights to [-127, 127], store as int8 (8 bits) → 4x multiplier
- **INT8 asymmetric**: use zero-point offset for better accuracy
- **INT4**: 4-bit → 8x multiplier, more accuracy loss
- **Mixed precision**: quantize large layers more aggressively
- **Per-channel**: separate scale per output neuron for better accuracy
