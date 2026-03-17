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

1. **Antithetic variates** — sample x and (1-x), their errors partially cancel
2. **Stratified sampling** — one sample per equal-width stratum
3. **Quasi-random sequences** — Halton/van der Corput, lower discrepancy than pseudo-random
