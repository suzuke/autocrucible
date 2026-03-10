"""Evaluation harness — DO NOT MODIFY.

Trains a character-level language model using MLX on Apple Silicon,
then evaluates validation bits-per-byte (val_bpb).

Bits per byte = cross_entropy_loss / ln(2)
This measures how many bits are needed per byte of text — lower is better.
Shannon entropy of English text is ~1.0-1.5 bpb; unigram baseline ~4.5 bpb.

Output format (parsed by crucible):
    val_bpb:        <float>
    train_loss:     <float>
    val_loss:       <float>
    elapsed_sec:    <float>
    param_count:    <int>
"""

import time
import math
import numpy as np
import mlx.core as mx
import mlx.nn as nn

from data import prepare_data


def count_parameters(model):
    """Count total trainable parameters."""
    import mlx.utils
    leaves = mlx.utils.tree_flatten(model.parameters())
    return sum(v.size for _, v in leaves)


def evaluate_bpb(model, data, vocab_size, context_len=256, batch_size=32):
    """Evaluate bits-per-byte on a dataset.

    Processes the data in non-overlapping chunks to get a complete evaluation.
    """
    n = len(data)
    total_loss = 0.0
    total_tokens = 0

    # Process in non-overlapping windows
    for start in range(0, n - context_len - 1, context_len * batch_size):
        batch_x = []
        batch_y = []
        for b in range(batch_size):
            offset = start + b * context_len
            if offset + context_len + 1 > n:
                break
            batch_x.append(data[offset:offset + context_len])
            batch_y.append(data[offset + 1:offset + context_len + 1])

        if not batch_x:
            break

        x = mx.array(np.stack(batch_x))
        y = mx.array(np.stack(batch_y))

        logits = model(x)
        loss = nn.losses.cross_entropy(
            logits.reshape(-1, vocab_size),
            y.reshape(-1),
            reduction="sum",
        )
        mx.eval(loss)

        total_loss += loss.item()
        total_tokens += y.size

    if total_tokens == 0:
        return float("inf")

    avg_loss = total_loss / total_tokens
    bpb = avg_loss / math.log(2)
    return bpb


if __name__ == "__main__":
    print("=== Language Model Evaluation ===")
    print()

    # Prepare data
    train_data, val_data, vocab_size, itos, stoi = prepare_data()

    # Import and build model
    from train import build_model, train as train_model, CONTEXT_LEN

    model = build_model(vocab_size)
    n_params = count_parameters(model)
    print(f"Parameters: {n_params:,}")
    print()

    # Train
    t0 = time.perf_counter()
    model = train_model(model, train_data, val_data, vocab_size)
    elapsed = time.perf_counter() - t0

    # Evaluate
    print()
    print("Evaluating...")
    train_bpb = evaluate_bpb(model, train_data, vocab_size, CONTEXT_LEN)
    val_bpb = evaluate_bpb(model, val_data, vocab_size, CONTEXT_LEN)

    # Convert bpb back to loss for reference
    train_loss = train_bpb * math.log(2)
    val_loss = val_bpb * math.log(2)

    print()
    print(f"val_bpb: {val_bpb:.6f}")
    print(f"train_loss: {train_loss:.6f}")
    print(f"val_loss: {val_loss:.6f}")
    print(f"elapsed_sec: {elapsed:.2f}")
    print(f"param_count: {n_params}")
