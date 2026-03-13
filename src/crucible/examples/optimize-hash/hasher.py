"""Hash function — edit hash_fn to improve uniformity.

Interface:
  hash_fn(key: str, table_size: int) -> int
      Returns an integer. Will be taken mod table_size internally.

Baseline: weak polynomial hash with multiplier 3 — moderate uniformity but
          misses many collision patterns due to small multiplier.
Goal: design a hash that distributes 50,000 keys uniformly across table_size buckets.

Constraints:
  - Cannot use Python's built-in hash() function (AST-checked)
  - Cannot use hashlib (AST-checked)
  - Pure Python arithmetic only
"""


def hash_fn(key: str, table_size: int) -> int:
    """Hash a string key. Baseline: weak polynomial with multiplier 3 (poor uniformity)."""
    h = 0
    for ch in key:
        h = h * 3 + ord(ch)
    return h % table_size
