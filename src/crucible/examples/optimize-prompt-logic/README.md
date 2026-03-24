# optimize-prompt-logic

Optimize a system prompt that helps Claude solve logical reasoning problems (syllogisms, modus ponens, fallacies).

**Requirements**: Anthropic API key (uses Claude for evaluation)

## What It Does

- Agent edits `prompt.txt` to craft a system prompt (max 2,000 chars) for logic reasoning
- Evaluation sends 10 hidden logic problems to Claude; answers are True, False, or Cannot determine
- Key challenge: distinguishing valid inferences from fallacies (e.g., affirming the consequent)

## Quick Start

```bash
crucible new my-prompt-logic -e optimize-prompt-logic
cd my-prompt-logic
crucible run --tag v1
```

## Metrics

- **Metric**: accuracy (maximize) -- 0.0 to 1.0
- **Baseline**: ~0.5 (minimal prompt)
- **Eval time**: ~10-20s (90s timeout, depends on API latency)
