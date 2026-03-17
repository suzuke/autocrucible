# optimize-monte-carlo Example Design

## Goal

Add an `optimize-monte-carlo` example that demonstrates the v0.5.0 stability validation feature: `crucible validate` detects high metric variance (CV > 5%) and auto-writes `evaluation.repeat: 3` to config.yaml.

## Architecture

A simple Monte Carlo integration experiment where the agent improves an estimator for ∫₀¹ f(x) dx. The true value is known, so absolute error is the metric. Plain Monte Carlo with 1000 samples produces high per-run variance (CV > 5%), making it a natural trigger for the stability check.

## Files

| File | Role | Agent access |
|------|------|-------------|
| `estimate.py` | Monte Carlo estimator | editable |
| `benchmark.py` | Runs estimator, prints `error: <value>` | hidden |

## Initial Implementation (`estimate.py`)

Plain Monte Carlo with 1000 uniform random samples:

```python
import random

def estimate():
    N = 1000
    total = sum(random.random() ** 2 for _ in range(N))
    return total / N  # estimates ∫₀¹ x² dx = 1/3
```

True value: 1/3 ≈ 0.3333. Expected single-run error: ~0.01–0.05. CV across runs: ~10–20% → stability check triggers.

## Benchmark (`benchmark.py`)

```python
from estimate import estimate
TRUE_VALUE = 1 / 3
result = estimate()
error = abs(result - TRUE_VALUE)
print(f"error: {error:.6f}")
```

## Config

```yaml
name: "optimize-monte-carlo"
files:
  editable: ["estimate.py"]
  hidden: ["benchmark.py"]
commands:
  run: "python3 -u benchmark.py > run.log 2>&1"
  eval: "grep '^error:' run.log"
metric:
  name: "error"
  direction: "minimize"
constraints:
  timeout_seconds: 30
  max_retries: 3
agent:
  instructions: "program.md"
```

## Agent Improvement Directions

Ordered by implementation complexity:
1. **Increase sample count** — immediate but bounded by timeout
2. **Antithetic variates** — sample x and 1-x together; negatively correlated, reduces variance
3. **Stratified sampling** — divide [0,1] into N equal strata, one sample per stratum
4. **Quasi-random sequences** — Halton or van der Corput sequences; much lower discrepancy than pseudo-random
5. **Importance sampling** — weight samples by a proposal distribution closer to f(x)

## Educational Path (README)

```bash
# Step 1: See the instability
crucible validate          # detects CV > 5%, auto-writes evaluation.repeat: 3

# Step 2: Run the optimizer
crucible run --tag mc-v1   # agent improves estimate.py

# Step 3: Compare
crucible history
```

The README explains: without `evaluation.repeat`, a "lucky" single run might look like an improvement when it's just noise. The stability check makes the optimization reliable.
