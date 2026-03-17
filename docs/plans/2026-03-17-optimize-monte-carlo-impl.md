# optimize-monte-carlo Example Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create an `optimize-monte-carlo` example that demonstrates the v0.5.0 stability validation feature — `crucible validate` detects CV > 5% and auto-writes `evaluation.repeat: 3`.

**Architecture:** A Monte Carlo integration experiment (∫₀¹ x² dx = 1/3). The agent improves `estimate.py`; `benchmark.py` (hidden) measures absolute error from the true value. Plain Monte Carlo with 1000 samples produces ~10–20% CV between runs, reliably triggering the stability check.

**Tech Stack:** Python stdlib only (random, math). No dependencies.

---

### Task 1: Create directory structure and config

**Files:**
- Create: `src/crucible/examples/optimize-monte-carlo/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-monte-carlo/.crucible/program.md`

**Step 1: Create `.crucible/config.yaml`**

```yaml
name: "optimize-monte-carlo"

files:
  editable:
    - "estimate.py"
  hidden:
    - "benchmark.py"

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

git:
  branch_prefix: "crucible"
```

**Step 2: Create `.crucible/program.md`**

```markdown
# Monte Carlo Integration Optimization

You are optimizing a Monte Carlo estimator for ∫₀¹ x² dx (true value = 1/3).

## Goal

Minimize `error` — the absolute difference between your estimate and 1/3.

## Rules

- Edit only `estimate.py`
- Your `estimate()` function must return a float
- The function signature must remain `def estimate() -> float:`
- Standard library only (random, math)

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `estimate.py`
```

**Step 3: Commit**

```bash
git add src/crucible/examples/optimize-monte-carlo/
git commit -m "feat: add optimize-monte-carlo example config and instructions"
```

---

### Task 2: Create the editable estimator (`estimate.py`)

**Files:**
- Create: `src/crucible/examples/optimize-monte-carlo/estimate.py`

**Step 1: Write `estimate.py`**

```python
import random


def estimate() -> float:
    """Estimate ∫₀¹ x² dx using plain Monte Carlo with 1000 samples."""
    N = 1000
    total = sum(random.random() ** 2 for _ in range(N))
    return total / N
```

**Step 2: Manually verify output**

Run: `cd src/crucible/examples/optimize-monte-carlo && python3 -c "from estimate import estimate; print(estimate())"`

Expected: a float close to 0.3333 (e.g. 0.3241, 0.3489 — varies each run)

**Step 3: Run twice and confirm variance**

```bash
python3 -c "from estimate import estimate; print(estimate())"
python3 -c "from estimate import estimate; print(estimate())"
```

Expected: two different values, difference often > 0.01

**Step 4: Commit**

```bash
git add src/crucible/examples/optimize-monte-carlo/estimate.py
git commit -m "feat: add optimize-monte-carlo estimate.py (plain Monte Carlo baseline)"
```

---

### Task 3: Create the hidden benchmark (`benchmark.py`)

**Files:**
- Create: `src/crucible/examples/optimize-monte-carlo/benchmark.py`

**Step 1: Write `benchmark.py`**

```python
from estimate import estimate

TRUE_VALUE = 1 / 3

result = estimate()
error = abs(result - TRUE_VALUE)
print(f"error: {error:.6f}")
```

**Step 2: Verify output format matches eval command**

Run: `python3 benchmark.py`

Expected output (one line matching `grep '^error:'`):
```
error: 0.023451
```

**Step 3: Run 5 times and check variance manually**

```bash
for i in $(seq 5); do python3 benchmark.py; done
```

Expected: 5 different error values. Typical range: 0.005–0.050. This high variance confirms CV > 5%.

**Step 4: Commit**

```bash
git add src/crucible/examples/optimize-monte-carlo/benchmark.py
git commit -m "feat: add optimize-monte-carlo benchmark.py"
```

---

### Task 4: Verify stability check triggers

**Files:** (no changes — just verification)

**Step 1: From the project root, run crucible validate**

```bash
cd src/crucible/examples/optimize-monte-carlo
crucible validate
```

Expected output includes:
```
Stability check: running 3 times...
  run 1: error = 0.023451
  run 2: error = 0.008234
  run 3: error = 0.031872
  CV = 18.3% (threshold: 5%)
  ⚠ High variance detected — auto-setting evaluation.repeat: 3
```

And `config.yaml` is updated to include `evaluation.repeat: 3`.

**Step 2: Confirm config.yaml was updated**

```bash
grep "repeat" .crucible/config.yaml
```

Expected: `  repeat: 3`

**Step 3: Also confirm `.crucible/.validated` marker was written**

```bash
ls .crucible/
```

Expected: `.validated` file present

**Note:** If CV happens to be < 5% on this run (unlikely but possible), re-run `crucible validate` once more.

---

### Task 5: Add README.md with demo walkthrough

**Files:**
- Create: `src/crucible/examples/optimize-monte-carlo/README.md`

**Step 1: Write `README.md`**

```markdown
# optimize-monte-carlo

Demonstrates **stability validation** (v0.5.0): crucible detects noisy metrics and automatically configures multi-run evaluation.

## The Problem

Monte Carlo integration estimates ∫₀¹ x² dx by random sampling. With only 1000 samples, each run produces a different error — the metric is noisy by design.

## Demo Walkthrough

### Step 1: Run stability check

```bash
crucible validate
```

crucible runs the experiment 3× and computes CV (coefficient of variation). With plain Monte Carlo, CV is typically 10–20%, well above the 5% threshold. It automatically writes `evaluation.repeat: 3` to config.yaml.

### Step 2: Run the optimizer

```bash
crucible run --tag mc-v1
```

The agent improves `estimate.py`. With `evaluation.repeat: 3`, each iteration runs 3× and reports the median — noise no longer misleads the keep/discard decision.

### Step 3: Review results

```bash
crucible history
```

## Why This Matters

Without `evaluation.repeat`, a "lucky" single run might show a 30% improvement that disappears on the next run. The stability check prevents chasing noise.

## Agent Improvement Directions

1. Antithetic variates — sample x and (1-x), their errors partially cancel
2. Stratified sampling — one sample per equal-width stratum
3. Quasi-random sequences — Halton/van der Corput, lower discrepancy than pseudo-random
```

**Step 2: Commit**

```bash
git add src/crucible/examples/optimize-monte-carlo/README.md
git commit -m "docs: add optimize-monte-carlo README with demo walkthrough"
```
