# autocrucible

[![PyPI](https://img.shields.io/pypi/v/autocrucible)](https://pypi.org/project/autocrucible/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

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
# Install as a global CLI tool
uv tool install autocrucible

# Or install from a local clone
git clone https://github.com/suzuke/autocrucible.git
uv tool install ./crucible
```

Verify:

```bash
crucible --help
```

### Updating

```bash
# From PyPI
uv tool install autocrucible --force

# From local source (after pulling changes)
uv tool install ./crucible --force
```

### For development

```bash
git clone https://github.com/suzuke/autocrucible.git
cd crucible
uv sync                 # install in local .venv
uv run crucible --help  # run from source
uv run pytest           # run tests
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
crucible run --tag run1    # auto-inits git repo, branch, and results
```

**Using the wizard (AI-generated scaffold):**

```bash
crucible wizard ~/my-experiment --describe "Train an AlphaZero Gomoku agent using NN and MCTS"
cd ~/my-experiment
crucible run --tag run1    # auto-inits if needed
```

The wizard analyzes your description, asks clarifying questions, and generates a complete project with **architecture guards** baked into `evaluate.py` — preventing the agent from bypassing your intended approach.

**From scratch:**

```bash
crucible new ~/my-experiment
cd ~/my-experiment
# Edit .crucible/config.yaml and program.md
crucible run --tag run1    # auto-inits if needed
```

If your experiment needs third-party packages (numpy, torch, etc.), they are listed in the generated `pyproject.toml`. Install them:

```bash
uv sync
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

### 2. Run

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

If interrupted, simply re-run the same command — crucible automatically detects the existing branch and resumes where it left off:

```bash
crucible run --tag run1   # resumes from previous state
```

### 4. Check results

```bash
crucible status
# Experiment: optimize-sorting
# Total: 15  Kept: 8  Discarded: 5  Crashed: 2
# Best ops_per_sec: 142000.0 (commit b2c3d4e)

crucible history --last 5
# Commit      Metric Status   Description
# ------------------------------------------------------------
# b2c3d4e   142000.0 keep     switch to radix sort for large arrays
# a1b2c3d   138000.0 keep     add insertion sort for small partitions
# ...

# JSON output for programmatic use
crucible status --json
crucible history --json --last 20

# Compare two experiment runs
crucible compare run1 run2
crucible compare run1 run2 --json
```

## How It Works

```
crucible run --tag run1
        │
        ▼
┌─────────────────────────────────┐
│  1. Assemble prompt             │  instructions + history + state
│  2. Claude Agent SDK            │  agent reads/edits files
│  3. Guard rails                 │  validate edits
│  4. Git commit                  │  snapshot the change
│  5. Run experiment              │  python evaluate.py > run.log
│  6. Parse metric                │  grep '^metric:' run.log
│  7. Keep or discard             │  improved? keep : reset
│  8. Loop                        │
└─────────────────────────────────┘
```

- **Agent**: Uses the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) with a tool allowlist (Read, Edit, Write, Glob, Grep). The agent can read files, make targeted edits, and search the codebase — but cannot execute arbitrary commands.
- **Environment**: If your project has a `.venv/`, crucible automatically activates it when running experiment commands, so `python3 evaluate.py` uses the correct interpreter and packages.
- **Git**: Every attempt is committed. Improvements advance the branch; failures are tagged and reset, preserving the diff for analysis.

### Postmortem analysis

After a run completes (or is interrupted), analyze what happened:

```bash
crucible postmortem                   # text report with trend chart
crucible postmortem --json            # machine-readable output
crucible postmortem --ai              # include AI-generated insights
```

The postmortem shows metric trends, failure streaks, and the best result. With `--ai`, Claude analyzes the iteration history and provides actionable insights about turning points, plateaus, and suggested next directions.

### Validate before running

```bash
crucible validate
#   [PASS] Config: config.yaml is valid
#   [PASS] Instructions: .crucible/program.md exists
#   [PASS] Editable files: All files exist
#   [PASS] Run command: Executed successfully
#   [PASS] Eval/metric: ops_per_sec: 42000.0
```

### Verbose logging

```bash
crucible -v run --tag run1   # debug-level output
```

## Config Reference

### `.crucible/config.yaml`

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

### Eval Command Convention

The eval command must output lines in `key: value` format:

```
metric_name: 0.12345
```

The platform extracts the value matching `metric.name`. This is compatible with common patterns like `grep '^loss:' run.log`.

### Single Metric by Design

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

### Git Strategy

- Each session runs on a branch: `<branch_prefix>/<tag>`
- Successful experiments advance the branch (commit stays)
- Failed experiments are tagged `failed/<tag>/<n>` then reset, preserving the diff for analysis
- `results.tsv` records every experiment regardless of outcome

### Guard Rails

**Pre-commit:** readonly files not modified, only listed files changed, at least one file edited.

**Post-execution:** timeout enforced (SIGTERM → SIGKILL), metric must be a valid number (not NaN/inf), consecutive failures capped at `max_retries`.

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
| `optimize-compress` | `compression_ratio` | maximize | Lossless text compression (no zlib/gzip allowed) |
| `optimize-gomoku` | `win_rate` | maximize | AlphaZero-style Gomoku agent training |
| `optimize-snake` | `avg_score` | maximize | Snake AI heuristic search (no dependencies) |

### Demo: optimize-compress

A showcase example where the agent builds a lossless text compressor from scratch:

```bash
crucible new ~/compress -e optimize-compress
cd ~/compress
crucible run --tag run1
```

Starting from a baseline RLE compressor (0.51x — worse than no compression), the agent typically:
- **Iter 1**: Implements LZ77 + Huffman → ~2.63x
- **Iter 2**: Adds optimal parsing DP + symbol remapping → ~2.81x (beats zlib's 2.65x)
- **Iter 3+**: Context modeling, arithmetic coding → 3.0x+

## Project Structure

```
my-experiment/
├── .crucible/
│   ├── config.yaml     # What to optimize, how to run, what to measure
│   └── program.md      # Instructions for the LLM agent
├── solution.py          # Code the agent modifies (editable)
├── evaluate.py          # Fixed harness that measures the metric (hidden)
├── pyproject.toml       # Experiment dependencies (NOT crucible itself)
├── results.tsv          # Auto-generated experiment log
└── run.log              # Latest experiment output
```

Crucible is installed as a **global CLI tool** — it is NOT a dependency of your experiment project. Your project's `pyproject.toml` only lists experiment-specific packages (numpy, torch, etc.).

## Claude Code Skill: Interactive Setup

Crucible ships with a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill that provides an interactive, guided workflow for creating experiment projects from scratch.

### Installing the skill

```bash
# Copy the skill to your Claude Code skills directory
cp -r /path/to/crucible/.claude/skills/crucible-setup ~/.claude/skills/
```

Or, if you cloned the crucible repo, add it to your project's `.claude/` directory:

```bash
mkdir -p .claude/skills
cp -r /path/to/crucible/.claude/skills/crucible-setup .claude/skills/
```

### Using the skill

Once installed, simply tell Claude Code what you want to optimize:

```
> I want to optimize a matrix multiplication algorithm
> Set up a new experiment to maximize inference throughput
> Create a benchmark for my sorting implementation
```

Claude Code will automatically activate the `crucible-setup` skill and walk you through a 7-step workflow:

1. **Define the metric** — what to measure, direction (min/max), dependencies
2. **Architecture constraints** — if you require a specific approach, the skill enforces it in `evaluate.py` (not just prompts) to prevent [Goodhart's Law](https://en.wikipedia.org/wiki/Goodhart%27s_law) violations
3. **Create evaluation harness** — hidden `evaluate.py` with correctness gating and method verification
4. **Create baseline** — simple, correct starting implementation
5. **Write agent instructions** — `program.md` with hard rules (code-enforced) vs soft rules (guidelines)
6. **Write config.yaml** — metric, commands, timeout, guard rails
7. **Verify baseline** — run the experiment to confirm everything works

### Why use the skill instead of examples?

| Approach | Best for |
|----------|----------|
| `crucible new -e <example>` | Standard problems similar to bundled examples |
| Claude Code skill | Custom problems, unique metrics, architecture constraints |

The skill is especially valuable when you have **architecture constraints** (e.g., "must use neural network", "implement with MCTS"). It generates `verify_method()` checks in the evaluation harness that zero the metric if the agent abandons the required approach — something you'd have to write manually otherwise.

## FAQ

### Won't the greedy strategy get stuck in local optima?

Crucible uses a greedy keep/discard loop — improvements are kept, regressions are discarded. This sounds like it could get stuck, but an LLM agent is fundamentally different from traditional optimization:

- The agent sees **full history** including discarded and crashed attempts, so it knows what didn't work and why
- It can reason about failures and deliberately try different architectural approaches, not just parameter tweaks
- It reads the actual code each iteration, so it can make structural changes that a blind search never would

That said, local optima is a real risk for long runs. The built-in escape hatch is **multiple tags** — essentially manual beam search:

```bash
# Explore different directions from the same baseline
crucible run --tag approach-a    # e.g. "focus on algorithmic improvements"
crucible run --tag approach-b    # e.g. "focus on low-level optimizations"
crucible compare approach-a approach-b
```

You can also backtrack to an earlier commit and branch from there:

```bash
git log crucible/run1              # find a promising commit
git checkout <commit>
crucible run --tag run1-variant    # auto-inits new branch from that point
crucible run --tag run1-variant
```

### Why only one metric? What about multi-objective optimization?

See [Single Metric by Design](#single-metric-by-design) above. The single scalar metric is a deliberate design choice that keeps the keep/discard decision unambiguous. Multi-objective trade-offs belong in your `evaluate.py`, where you have full domain knowledge to define what "better" means.

### Why not run multiple agents in parallel?

Crucible runs one agent per tag, serially. This is deliberate:

- **Cost efficiency**: Parallel agents multiply API costs, but serial agents learn from history — iteration N+1 is smarter than N because it sees what worked and what didn't. Blind parallel exploration doesn't have this advantage.
- **Simplicity**: Parallel agents editing the same files in the same repo cause git conflicts. Solving this requires worktree isolation, result synchronization, and merge strategies — significant complexity for marginal gain.

**The manual approach covers most needs.** Run multiple tags in separate terminals:

```bash
# Terminal 1                        # Terminal 2
crucible run --tag algo-focus       crucible run --tag lowlevel-focus
```

Each tag is an independent experiment branch. Compare results when done:

```bash
crucible compare algo-focus lowlevel-focus
```

This gives you full control over which directions to explore in parallel, with zero additional complexity.

### Is it safe to let the agent modify code that gets executed?

The agent cannot run arbitrary commands — it only has access to Read, Edit, Write, Glob, and Grep tools. However, the code it writes into editable files **is** executed by `commands.run`. If the editable file can make network requests, delete files, or perform other dangerous operations, guard rails won't catch that.

**Mitigations:**

- **Scope the editable files narrowly.** If `sort.py` only contains a sort function, the blast radius is limited even if the agent writes bad code.
- **Always set the evaluation harness as `hidden`, not `readonly`.** Readonly files are readable — the agent **will** study them and exploit implementation details (fixed seeds, scoring formulas, test data) to game the metric. In the `optimize-regression` example, the agent read `evaluate.py`, found `seed=42`, reconstructed the exact noise vector, and achieved MSE=0.0 in 3 iterations by memorizing the test set instead of learning regression. Hidden files are moved out of reach during agent execution but restored for the experiment subprocess.
- **Use `constraints.timeout_seconds`** to kill runaway experiments.
- **Run in a container or VM** for untrusted workloads. Crucible doesn't require root or network access.
- **Review the git log.** Every change is committed — you can audit exactly what the agent did.

This is the same trust model as CI/CD: you review the code, the system runs it. Crucible just automates the iteration loop.

### Where's the web dashboard?

There isn't one — by design. `results.tsv` is a plain TSV file that any tool can read, and experiments typically run tens of iterations, not thousands. A full web UI would be a separate project-sized effort for marginal benefit.

**Live monitoring** (in a separate terminal):

```bash
watch -n 5 crucible status
watch -n 5 crucible history --last 10
```

**Quick trend chart:**

```bash
# ASCII chart with gnuplot
tail -n +2 results.tsv | cut -f2 | gnuplot -e "set terminal dumb; plot '-' with lines"

# Or Python
python3 -c "
import csv
with open('results.tsv') as f:
    for i, x in enumerate(csv.DictReader(f, delimiter='\t')):
        bar = '#' * int(float(x['metric_value']) / 10)
        print(f'{i+1:3d} {float(x[\"metric_value\"]):8.2f} {bar}')
"
```

**Programmatic access:**

```bash
crucible status --json | jq .
crucible history --json --last 50 | jq '.[].metric'
```
