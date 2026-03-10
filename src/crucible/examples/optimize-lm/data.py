"""Data preparation — DO NOT MODIFY.

Downloads TinyShakespeare (or uses cached version) and prepares
character-level train/val splits.
"""

import os
import urllib.request
import numpy as np

DATA_URL = "https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt"
DATA_PATH = os.path.join(os.path.dirname(__file__), "input.txt")
SEED = 42
VAL_FRACTION = 0.1


def download_data():
    """Download TinyShakespeare if not cached."""
    if not os.path.exists(DATA_PATH):
        print(f"Downloading TinyShakespeare to {DATA_PATH}...")
        urllib.request.urlretrieve(DATA_URL, DATA_PATH)
    with open(DATA_PATH, "r") as f:
        text = f.read()
    return text


def prepare_data():
    """Prepare train/val splits and vocabulary.

    Returns:
        train_data: np.ndarray of int32 token ids (train split)
        val_data: np.ndarray of int32 token ids (val split)
        vocab_size: int — number of unique characters
        itos: dict mapping int -> char
        stoi: dict mapping char -> int
    """
    text = download_data()

    # Build vocabulary from full text
    chars = sorted(set(text))
    vocab_size = len(chars)
    stoi = {c: i for i, c in enumerate(chars)}
    itos = {i: c for c, i in stoi.items()}

    # Encode
    data = np.array([stoi[c] for c in text], dtype=np.int32)

    # Split
    n = len(data)
    n_val = int(n * VAL_FRACTION)
    n_train = n - n_val
    train_data = data[:n_train]
    val_data = data[n_train:]

    print(f"Corpus: {n} chars, vocab: {vocab_size}")
    print(f"Train: {n_train} chars, Val: {n_val} chars")

    return train_data, val_data, vocab_size, itos, stoi
