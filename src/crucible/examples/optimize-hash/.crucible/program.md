# Hash Function Optimization

You are designing a hash function that distributes string keys uniformly.

## Goal

Maximize `uniformity_score` — how evenly 50,000 keys are distributed across 65,537 buckets.
Score ranges from 0 (terrible) to 1 (perfect uniform distribution).

## Interface

```python
def hash_fn(key: str, table_size: int) -> int:
    """Return an integer. Will be taken mod table_size internally."""
```

## Rules

- Edit only `hasher.py`
- Cannot use Python's built-in `hash()` function (AST-checked)
- Cannot use `hashlib` (AST-checked)
- Pure Python arithmetic only (`ord()`, `abs()`, bit operations are fine)
- Standard library math/random are allowed

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `hasher.py`

## Key Distribution

See `key_sample.txt` for 100 example keys. The full test set includes:
- English word + number combinations (e.g. "apple42")
- UUID-style hex strings (e.g. "3f2a1b0c-dead-beef")
- Numeric strings (e.g. "7391842")

## Strategy

Good hash functions mix bits thoroughly so that similar keys map to different buckets:
- Polynomial rolling hash: `h = h * PRIME + ord(ch)`
- FNV-1a: XOR then multiply by a large prime
- Bit mixing: shifts, XORs, multiplications
The choice of prime and mixing constants matters significantly.
