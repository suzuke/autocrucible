import time
import random
import string
from cipher import encrypt

# Deterministic test data
random.seed(42)
TEXT = "".join(random.choices(string.printable, k=1_000_000))
KEY = {c: string.printable[(i + 13) % len(string.printable)]
       for i, c in enumerate(string.printable)}

start = time.perf_counter()
result = encrypt(TEXT, KEY)
elapsed = time.perf_counter() - start

# Correctness check
assert len(result) == len(TEXT), "Length mismatch"

throughput = len(TEXT) / elapsed
print(f"throughput: {throughput:.0f}")
