# optimize-cipher Example Design

## Goal

Demonstrate `search.strategy: restart`: the agent naturally optimizes loop-based cipher until plateau, then restart resets to baseline allowing it to discover `str.translate()` — a fundamentally different approach 10× faster.

## Problem

Substitution cipher: encrypt 1 MB of text by mapping each character to another via a key dict. Metric: `throughput` (chars/sec), maximize.

## Files

| File | Role | Agent access |
|------|------|-------------|
| `cipher.py` | Encryption function | editable |
| `benchmark.py` | Generates 1MB text, measures throughput | hidden |

## Initial Implementation (`cipher.py`)

```python
def encrypt(text: str, key: dict) -> str:
    result = []
    for char in text:
        result.append(key.get(char, char))
    return "".join(result)
```

This approach's ceiling: ~30–40 MB/s with optimizations (list vs string concat, conditionals). Hitting this ceiling in 3–4 iterations triggers the plateau.

## Restart Trigger

```yaml
search:
  strategy: restart
  plateau_threshold: 4   # short plateau for demo — 4 stagnant iters before reset
```

After restart, agent sees full history (knows loop-optimization was tried) and is likely to discover `str.translate()`:

```python
def encrypt(text: str, key: dict) -> str:
    table = str.maketrans(key)
    return text.translate(table)
```

This breaks through to ~200+ MB/s — the true global optimum.

## Benchmark (`benchmark.py`)

```python
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
_ = encrypt(TEXT, KEY)
elapsed = time.perf_counter() - start

throughput = len(TEXT) / elapsed
print(f"throughput: {throughput:.0f}")
```

## Config

```yaml
name: "optimize-cipher"
files:
  editable: ["cipher.py"]
  hidden: ["benchmark.py"]
commands:
  run: "python3 -u benchmark.py > run.log 2>&1"
  eval: "grep '^throughput:' run.log"
metric:
  name: "throughput"
  direction: "maximize"
constraints:
  timeout_seconds: 30
  max_retries: 3
search:
  strategy: restart
  plateau_threshold: 4
agent:
  instructions: "program.md"
```

## Expected Learning Path

1. Iterations 1–4: agent optimizes the loop (list comprehension, caching, early returns) — reaches ~35 MB/s
2. plateau_threshold=4 triggers restart
3. After restart: agent sees history ("loop optimization reached ceiling") and tries `str.translate()`
4. Throughput jumps to 200+ MB/s — new best

## Educational Point

Restart is not just "retry" — it resets the code AND retains history. The agent knows exactly what failed and why, so it explores a genuinely different algorithmic family.
