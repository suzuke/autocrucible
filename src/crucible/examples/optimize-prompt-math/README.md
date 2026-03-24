# optimize-prompt-math

Optimize a system prompt that helps Claude solve math word problems involving percentages, rates, ratios, and geometry.

**Requirements**: Anthropic API key (uses Claude for evaluation)

## What It Does

- Agent edits `prompt.txt` to craft a system prompt (max 2,000 chars) for math word problems
- Evaluation sends 10 hidden math problems to Claude and checks answers against exact numeric ground truth
- Problems involve percentages, rates, ratios, and geometry basics

## Quick Start

```bash
crucible new my-prompt-math -e optimize-prompt-math
cd my-prompt-math
crucible run --tag v1
```

## Metrics

- **Metric**: accuracy (maximize) -- 0.0 to 1.0
- **Baseline**: ~0.5 (minimal prompt)
- **Eval time**: ~10-20s (90s timeout, depends on API latency)
