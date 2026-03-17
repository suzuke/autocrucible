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

```bash
# From example
crucible new ~/my-project -e optimize-sorting
cd ~/my-project
crucible run --tag run1
crucible run --tag run1 --max-iterations 5   # stop after 5 iterations

# Check results
crucible status --tag run1
crucible history --tag run1
crucible postmortem --tag run1

# Continue from best result
crucible run --tag run2
```

See `crucible new . --list` for all examples, or `crucible wizard` for AI-generated projects.

If your experiment needs third-party packages (numpy, torch, etc.), install them with `uv sync` in the project directory.

### Validate before running

```bash
crucible validate
crucible validate --stability --runs 5    # check metric variance
```

### Verbose logging

```bash
crucible -v run --tag run1   # debug-level output
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
| `optimize-monte-carlo` | `error` | minimize | Monte Carlo integration — showcases stability validation |
| `optimize-cipher` | `throughput` | maximize | Substitution cipher — showcases restart strategy |
| `optimize-pathfind` | `nodes_explored` | minimize | Grid pathfinding — showcases beam strategy |

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

### v0.5.0 Feature Showcase Examples

Three examples that demonstrate the v0.5.0 search strategy and stability features:

#### optimize-monte-carlo — Stability Validation

Monte Carlo integration of ∫₀¹ x² dx. Each run uses different random samples, so the metric varies by ~30–40% between runs — exactly the scenario that makes single-run evaluation unreliable.

```bash
crucible new ~/mc -e optimize-monte-carlo
cd ~/mc
crucible validate          # detects CV ~36% > 5%, auto-writes evaluation.repeat: 3
crucible run --tag mc-v1   # now each iteration runs 3× and reports median
```

The stability check prevents the agent from chasing noise: without `evaluation.repeat`, a "lucky" run looks like an improvement even when nothing changed.

#### optimize-cipher — Restart Strategy

Substitution cipher on 1 MB of text. The loop-based baseline can be optimized (list comprehension, caching) to ~55 MB/s — but `str.translate()` runs at 200+ MB/s and is a completely different approach that greedy search won't reach on its own.

```bash
crucible new ~/cipher -e optimize-cipher
cd ~/cipher
crucible run --tag cipher-v1
```

With `plateau_threshold: 4`, after 4 stagnant iterations the platform resets to the original code and injects full history. The agent sees "loop optimizations reached ceiling" and explores `str.translate()` — a ~4× breakthrough.

**Key insight:** Restart is not "retry". The code resets, but the agent retains full history and knows exactly which directions are exhausted.

#### optimize-pathfind — Beam Strategy

BFS pathfinding on 100 random 20×20 grids. BFS visits ~40–70% of grid cells; A* with Manhattan heuristic visits ~10–20%; jump-point search is even more efficient.

```bash
crucible new ~/pathfind -e optimize-pathfind
cd ~/pathfind
crucible run --tag pathfind-v1
```

With `beam_width: 3`, three independent branches explore different algorithm families. Each beam sees a compact summary of what others tried — if beam-0 found bidirectional BFS and beam-1 found A*, beam-2 won't waste iterations reimplementing them.

**Key insight:** Beam is serial (one agent at a time, cost proportional to iterations). The advantage is exploration breadth, not speed.

## Project Structure

```
my-experiment/
├── .crucible/
│   ├── config.yaml     # What to optimize, how to run, what to measure
│   └── program.md      # Instructions for the LLM agent
├── solution.py          # Code the agent modifies (editable)
├── evaluate.py          # Fixed harness that measures the metric (hidden)
├── pyproject.toml       # Experiment dependencies (NOT crucible itself)
├── results-{tag}.jsonl  # Auto-generated experiment log (per run)
├── run.log              # Latest experiment output
└── logs/                # Per-iteration logs
    └── iter-1/
        ├── agent.txt    # Agent reasoning
        └── run.log      # Experiment output
```

Crucible is installed as a **global CLI tool** — it is NOT a dependency of your experiment project. Your project's `pyproject.toml` only lists experiment-specific packages (numpy, torch, etc.).

## Documentation

- [Config Reference](docs/CONFIG.md) — all YAML fields, eval convention, git strategy, guard rails
- [FAQ](docs/FAQ.md) — local optima, single metric, parallel agents, safety, monitoring
- [Changelog](docs/CHANGELOG.md) — version history and release notes
