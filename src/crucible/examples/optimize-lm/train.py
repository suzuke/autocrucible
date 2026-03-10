"""Language model — this is the file the agent optimizes.

Baseline: 6-layer, 8-head, 256-dim character-level GPT with Adam.
Intentionally under-trained (500 steps) to leave room for optimization.
"""

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
import numpy as np
import math

# --- Hyperparameters ---
CONTEXT_LEN = 256
N_LAYERS = 6
N_HEADS = 8
N_EMBD = 256
LEARNING_RATE = 3e-4
BATCH_SIZE = 32
MAX_STEPS = 500


class CausalSelfAttention(nn.Module):
    def __init__(self, n_embd, n_heads):
        super().__init__()
        assert n_embd % n_heads == 0
        self.n_heads = n_heads
        self.head_dim = n_embd // n_heads
        self.qkv_proj = nn.Linear(n_embd, 3 * n_embd)
        self.out_proj = nn.Linear(n_embd, n_embd)

    def __call__(self, x):
        B, T, C = x.shape
        qkv = self.qkv_proj(x)
        q, k, v = mx.split(qkv, 3, axis=-1)

        q = q.reshape(B, T, self.n_heads, self.head_dim).transpose(0, 2, 1, 3)
        k = k.reshape(B, T, self.n_heads, self.head_dim).transpose(0, 2, 1, 3)
        v = v.reshape(B, T, self.n_heads, self.head_dim).transpose(0, 2, 1, 3)

        scale = math.sqrt(self.head_dim)
        attn = (q @ k.transpose(0, 1, 3, 2)) / scale

        # Causal mask
        mask = mx.triu(mx.full((T, T), -1e9), k=1)
        attn = attn + mask

        attn = mx.softmax(attn, axis=-1)
        out = (attn @ v).transpose(0, 2, 1, 3).reshape(B, T, C)
        return self.out_proj(out)


class MLP(nn.Module):
    def __init__(self, n_embd):
        super().__init__()
        self.fc1 = nn.Linear(n_embd, 4 * n_embd)
        self.fc2 = nn.Linear(4 * n_embd, n_embd)

    def __call__(self, x):
        return self.fc2(nn.gelu(self.fc1(x)))


class TransformerBlock(nn.Module):
    def __init__(self, n_embd, n_heads):
        super().__init__()
        self.ln1 = nn.LayerNorm(n_embd)
        self.attn = CausalSelfAttention(n_embd, n_heads)
        self.ln2 = nn.LayerNorm(n_embd)
        self.mlp = MLP(n_embd)

    def __call__(self, x):
        x = x + self.attn(self.ln1(x))
        x = x + self.mlp(self.ln2(x))
        return x


class GPT(nn.Module):
    def __init__(self, vocab_size, n_embd, n_heads, n_layers, context_len):
        super().__init__()
        self.token_emb = nn.Embedding(vocab_size, n_embd)
        self.pos_emb = nn.Embedding(context_len, n_embd)
        self.blocks = [TransformerBlock(n_embd, n_heads) for _ in range(n_layers)]
        self.ln_f = nn.LayerNorm(n_embd)
        self.head = nn.Linear(n_embd, vocab_size)

    def __call__(self, x):
        B, T = x.shape
        tok_emb = self.token_emb(x)
        pos_emb = self.pos_emb(mx.arange(T))
        x = tok_emb + pos_emb
        for block in self.blocks:
            x = block(x)
        x = self.ln_f(x)
        return self.head(x)


def build_model(vocab_size):
    """Build and return the language model.

    Args:
        vocab_size: number of unique tokens in vocabulary

    Returns:
        mlx.nn.Module — the model
    """
    model = GPT(vocab_size, N_EMBD, N_HEADS, N_LAYERS, CONTEXT_LEN)
    mx.eval(model.parameters())
    return model


def get_batch(data, batch_size, context_len):
    """Sample a random batch of (input, target) sequences."""
    ix = np.random.randint(0, len(data) - context_len - 1, size=batch_size)
    x = mx.array(np.stack([data[i:i + context_len] for i in ix]))
    y = mx.array(np.stack([data[i + 1:i + context_len + 1] for i in ix]))
    return x, y


def train(model, train_data, val_data, vocab_size):
    """Train the model and return it.

    Args:
        model: mlx.nn.Module from build_model()
        train_data: np.ndarray of int32 token ids
        val_data: np.ndarray of int32 token ids
        vocab_size: int

    Returns:
        The trained model
    """
    optimizer = optim.Adam(learning_rate=LEARNING_RATE)

    def loss_fn(model, x, y):
        logits = model(x)
        loss = nn.losses.cross_entropy(
            logits.reshape(-1, vocab_size),
            y.reshape(-1),
            reduction="mean",
        )
        return loss

    loss_and_grad = nn.value_and_grad(model, loss_fn)

    for step in range(MAX_STEPS):
        x, y = get_batch(train_data, BATCH_SIZE, CONTEXT_LEN)
        loss, grads = loss_and_grad(model, x, y)
        optimizer.update(model, grads)
        mx.eval(model.parameters(), optimizer.state)

        if step % 100 == 0:
            print(f"step {step:5d} | train_loss: {loss.item():.4f}")

    return model
