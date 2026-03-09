# crucible

[繁體中文](README.zh-TW.md) | English

A general-purpose autonomous experiment platform. Define what to edit, how to run, and what to measure — then let an LLM agent iterate indefinitely to optimize your metric.

## Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** — Python package manager
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # or via Homebrew
  brew install uv
  ```
- **Git** — the platform uses git for version control of experiments
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** — the `claude` CLI must be installed and authenticated
  ```bash
  # Install
  npm install -g @anthropic-ai/claude-code

  # Authenticate (follow the prompts)
  claude
  ```

## Install

```bash
uv sync
```

This installs the `crucible` CLI command. Verify:

```bash
crucible --help
```

## Quick Start

### 1. Create a project

**From an example:**

```bash
# List available examples
crucible new . --list

# Create from example
crucible new ~/my-experiment -e optimize-sorting
cd ~/my-experiment
git init && git add -A && git commit -m 'initial'
```

**From scratch:**

```bash
crucible new ~/my-experiment
cd ~/my-experiment
# Edit .crucible/config.yaml and program.md
git init && git add -A && git commit -m 'initial'
```

**Or manually** — in your project repo, create `.crucible/config.yaml`:

```yaml
name: "optimize-sorting"
description: "Find the fastest sorting implementation"

files:
  editable:
    - "sort.py"
  readonly:
    - "benchmark.py"

commands:
  run: "python benchmark.py > run.log 2>&1"
  eval: "grep '^ops_per_sec:' run.log"

metric:
  name: "ops_per_sec"
  direction: "maximize"
```

And `.crucible/program.md` with instructions for the agent:

```markdown
You are optimizing a sorting algorithm.
Edit sort.py to improve throughput measured by ops_per_sec.
Try different algorithms, data structures, and optimizations.
```

### 2. Initialize

```bash
crucible init --tag run1
```

This creates a git branch `crucible/run1` and initializes `results.tsv`.

### 3. Run

```bash
crucible run --tag run1
```

The platform will loop indefinitely:
1. Ask the agent to propose and implement one change
2. Validate the edit (only allowed files modified)
3. Commit and run the experiment
4. Parse the metric
5. Keep if improved, discard if not
6. Repeat

Press `Ctrl+C` to stop gracefully (waits for current experiment to finish).

### 4. Check results

```bash
crucible status
# Total: 15  Kept: 8  Discarded: 5  Crashed: 2
# Best ops_per_sec: 142000.0 (commit b2c3d4e)

crucible history --last 5
# Commit      Metric Status   Description
# ------------------------------------------------------------
# b2c3d4e   142000.0 keep     switch to radix sort for large arrays
# a1b2c3d   138000.0 keep     add insertion sort for small partitions
# ...
```

## Config Reference

### `.crucible/config.yaml`

```yaml
# Required
name: "experiment-name"                    # Experiment identifier
files:
  editable: ["train.py"]                   # Files the agent can modify
  readonly: ["eval.py"]                    # Files the agent must not touch (optional)
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
  max_retries: 3                           # Max retries on crash/violation
agent:
  type: "claude-code"                      # Agent backend
  instructions: "program.md"              # Static instructions file
  context_window:
    include_history: true                  # Inject past experiment results
    history_limit: 20                      # Max history entries in prompt
    include_best: true                     # Show current best metric
git:
  branch_prefix: "crucible"                # Branch: <prefix>/<tag>
  tag_failed: true                         # Tag failed experiments before reset
```

### Eval Command Convention

The eval command must output lines in `key: value` format:

```
metric_name: 0.12345
```

The platform extracts the value matching `metric.name`. This is compatible with common patterns like `grep '^loss:' run.log`.

### Git Strategy

- Each session runs on a branch: `<branch_prefix>/<tag>`
- Successful experiments advance the branch (commit stays)
- Failed experiments are tagged `failed/<tag>/<n>` then reset, preserving the diff for analysis
- `results.tsv` records every experiment regardless of outcome

### Guard Rails

**Pre-commit:** readonly files not modified, only listed files changed, at least one file edited.

**Post-execution:** timeout enforced (SIGTERM → SIGKILL), metric must be a valid number (not NaN/inf).

### Context Assembly

Each iteration, the agent receives a dynamically assembled prompt:

1. **Static instructions** from `program.md`
2. **Current state** — branch, best metric, experiment counts
3. **Experiment history** — recent results table + observed patterns
4. **Action directive** — "propose and implement ONE experiment"
5. **Error/crash context** — if the previous iteration failed, the error is included

## Examples

Bundled examples to get started quickly. Create a project from any example:

```bash
crucible new ~/my-project -e <example-name>
```

| Example | Metric | Direction | Description |
|---------|--------|-----------|-------------|
| `optimize-sorting` | `ops_per_sec` | maximize | Pure Python sorting throughput optimization |
| `optimize-regression` | `val_mse` | minimize | Synthetic regression with nonlinear interactions |
| `optimize-classifier` | `val_accuracy` | maximize | Numpy-only neural network on 8-class dataset |
| `optimize-gomoku` | `win_rate` | maximize | AlphaZero-style Gomoku agent training |
