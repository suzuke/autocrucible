"""Hash function — edit hash_fn to improve uniformity.

Interface:
  hash_fn(key: str, table_size: int) -> int
      Returns an integer. Will be taken mod table_size internally.

Baseline: polynomial hash with small prime — gets moderate uniformity.
Goal: design a hash that distributes 50,000 keys uniformly across table_size buckets.

Constraints:
  - Cannot use Python's built-in hash() function (AST-checked)
  - Cannot use hashlib (AST-checked)
  - Pure Python arithmetic only
"""


def hash_fn(key: str, table_size: int) -> int:
    """Hash a string key. Baseline: polynomial rolling hash."""
    h = 0
    for ch in key:
        h = h * 31 + ord(ch)
    return h % table_size
