# Config Reference

## `.crucible/config.yaml`

```yaml
# Required
name: "experiment-name"                    # Experiment identifier
files:
  editable: ["train.py"]                   # Files the agent can modify
  readonly: ["data.py"]                    # Agent can read but not modify (optional)
  hidden: ["evaluate.py"]                  # Invisible to agent; available to subprocess
commands:
  run: "python train.py > run.log 2>&1"    # How to run one experiment
  eval: "grep '^metric:' run.log"          # How to extract the metric
metric:
  name: "metric"                           # Metric key (matches eval output)
  direction: "minimize"                    # "minimize" or "maximize"

# Optional (defaults shown)
description: ""                            # Human-readable description
commands:
  setup: "pip install -r requirements.txt" # One-time setup (run on init)
constraints:
  timeout_seconds: 600                     # Kill experiment after this
  max_retries: 3                           # Max consecutive failures before stop
  plateau_threshold: 8                     # Consecutive stagnant iters before strong prompt
  allow_install: false                     # Let agent add packages via requirements.txt
  budget:                                  # Cost tracking
    max_cost_usd: 10.0
    max_cost_per_iter_usd: 0.50
    warn_at_percent: 80
evaluation:                                # Multi-run evaluation
  repeat: 1                                # Runs per iteration (1 = single run)
  aggregation: "median"                    # median | mean
sandbox:                                   # Docker isolation
  backend: "none"                          # docker | none
  base_image: "python:3.12-slim"
  network: false
  memory_limit: "2g"
  cpu_limit: 2
agent:
  type: "claude-code"                      # Agent backend
  instructions: "program.md"              # Static instructions file
  system_prompt: "system.md"              # Custom system prompt (optional, default: built-in)
  context_window:
    include_history: true                  # Inject past experiment results
    history_limit: 20                      # Max history entries in prompt
    include_best: true                     # Show current best metric
git:
  branch_prefix: "crucible"                # Branch: <prefix>/<tag>
  tag_failed: true                         # Tag failed experiments before reset
```

## Eval Command Convention

The eval command must output lines in `key: value` format:

```
metric_name: 0.12345
```

The platform extracts the value matching `metric.name`. This is compatible with common patterns like `grep '^loss:' run.log`.

## Single Metric by Design

Crucible uses a single scalar metric — this is a deliberate design choice, not a limitation. A single number makes the keep/discard decision unambiguous, keeps the loop simple and reliable, and forces you to define "better" clearly in your evaluation harness.

**Multi-objective optimization** is handled in `evaluate.py`, not the platform:

```python
latency = measure_latency()
throughput = measure_throughput()

# Weighted combination
metric = throughput / latency

# Constraint-based (zero the metric if a constraint is violated)
metric = throughput if latency < 100 else 0

# Staged (correctness first, then optimize)
metric = throughput if correctness == 1.0 else -1000

print(f"metric: {metric}")
```

This keeps complexity in your domain logic (where it belongs) rather than in the platform.

## Git Strategy

- Each session runs on a branch: `<branch_prefix>/<tag>`
- Successful experiments advance the branch (commit stays)
- Failed experiments are tagged `failed/<tag>/<n>` then reset, preserving the diff for analysis
- `results-{tag}.jsonl` records every experiment regardless of outcome

## Guard Rails

**Pre-commit:** readonly files not modified, only listed files changed, at least one file edited.

**Post-execution:** timeout enforced (SIGTERM → SIGKILL), metric must be a valid number (not NaN/inf), consecutive failures capped at `max_retries`.

## Context Assembly

Each iteration, the agent receives a dynamically assembled prompt:

1. **Static instructions** from `program.md`
2. **Current state** — branch, best metric, experiment counts
3. **Experiment history** — recent results table + observed patterns
4. **Action directive** — "propose and implement ONE experiment"
5. **Error/crash context** — if the previous iteration failed, the error is included
